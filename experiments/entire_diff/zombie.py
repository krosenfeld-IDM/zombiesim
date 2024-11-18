import starsim as ss
import sciris as sc
import numpy as np

class Zombie(ss.SIR):
    """ Extend the base SIR class to represent Zombies! """
    def __init__(self, pars=None, **kwargs):
        super().__init__()
        
        # Define parameters, inheriting defaults from SIR
        self.define_pars(
            inherit = True,
            dur_inf = ss.constant(1000), # Once a zombie, always a zombie!
            p_fast = ss.bernoulli(p=0.10), # Probability of being fast
            dur_fast = ss.constant(1000), # Duration of fast before becoming slow
            p_symptomatic = ss.bernoulli(p=1.0), # Probability of symptoms
            p_death_on_zombie_infection = ss.bernoulli(p=0.25), # Probability of death at time of infection
            p_death = ss.bernoulli(p=1), # All zombies die instead of recovering
        )
        self.update_pars(pars, **kwargs)

        # Define states
        self.define_states(
            ss.State('fast', default=self.pars.p_fast, label='Zombies who are fast'),
            ss.State('symptomatic', label='Symptomatic'),
            ss.FloatArr('ti_slow'), # Time index of changing from fast to slow
        )

        # Counters for reporting
        self.cum_congenital = 0 # Count cumulative congenital cases
        self.cum_deaths = 0 # Count cumulative deaths

        return

    def step_state(self):
        """ Updates states before transmission on this timestep """
        self.cum_deaths += np.count_nonzero(self.sim.people.ti_dead <= self.ti)

        super().step_state()

        # Transition from fast to slow
        fast_to_slow_uids = (self.infected & self.fast & (self.ti_slow <= self.ti)).uids
        self.fast[fast_to_slow_uids] = False

        return

    def set_prognoses(self, uids, sources=None):
        """ Set prognoses of new zombies """
        super().set_prognoses(uids, sources)

        # Choose which new zombies will be symptomatic
        self.symptomatic[uids] = self.pars.p_symptomatic.rvs(uids)

        # Set timer for fast to slow transition
        fast_uids = uids[self.fast[uids]]
        dur_fast = self.pars.dur_fast.rvs(fast_uids)
        self.ti_slow[fast_uids] = self.ti + dur_fast

        # Handle possible immediate death on zombie infection
        dead_uids = self.pars.p_death_on_zombie_infection.filter(uids)
        self.cum_deaths += len(dead_uids)
        self.sim.people.request_death(dead_uids)
        return

    def set_congenital(self, target_uids, sources=None):
        """ Handle congenital zombies """
        self.cum_congenital += len(target_uids)
        self.set_prognoses(target_uids, sources)
        return

    def init_results(self):
        """ Initialize results """
        super().init_results()
        self.define_results(
            ss.Result('cum_congenital', label='Cumulative congenital infections', dtype=int, scale=True),
            ss.Result('cum_deaths', label='Cumulative deaths', dtype=int, scale=True),
        )
        return

    def update_results(self):
        """ Update results on each time step """
        super().update_results()
        res = self.results
        ti = self.ti
        res.cum_congenital[ti] = self.cum_congenital
        res.cum_deaths[ti] = self.cum_deaths
        return


class DeathZombies(ss.Deaths):
    """ 
    Extension of Deaths to make some agents who die turn into zombies 
    """
    def __init__(self, pars=None, metadata=None, **kwargs):
        super().__init__(pars=pars, **kwargs)
        self.define_pars(
            p_zombie_on_natural_death = ss.bernoulli(p=0.75), # Probability of becoming a zombie on death
        )
        return

    def step(self):
        """ 
        Overwrite step to manage zombie transformation 
        """
        # Prevent zombies from dying of natural causes
        not_zombie = self.sim.people.alive.asnew() 
        for name, disease in self.sim.people.diseases.items():
            if 'zombie' in name:
                not_zombie = not_zombie & (~disease.infected) 

        # Determine sets of UIDs that will die and those that will turn into zombies
        death_uids = self.pars.death_rate.filter(not_zombie.uids)
        zombie_uids, death_uids = self.pars.p_zombie_on_natural_death.filter(death_uids, both=True)

        # Remove agents set to die from the alive population
        if len(death_uids):
            self.sim.people.request_death(death_uids)

        # Transform set agents into zombies
        if len(zombie_uids) > 0:
            # Determine 'zombie' disease type
            zombie = 'zombie' if 'zombie' in self.sim.diseases else 'slow_zombie'
            self.sim.diseases[zombie].set_prognoses(zombie_uids)

        return len(death_uids)


class KillZombies(ss.Intervention):
    """ Intervention that kills symptomatic zombies at a user-specified rate """

    def __init__(self, year=None, rate=None, **kwargs):
        year = sc.promotetoarray(year)
        rate = sc.promotetoarray(rate)
        pars = dict(
            year = year,
            rate = rate,
            p = ss.bernoulli(p=0)  # Placeholder value
        )
        super().__init__(pars=pars, **kwargs)
        self.requires = Zombie
        return

    def init_pre(self, sim):
        super().init_pre(sim)
        # Update any time-dependent parameters based on the sim's timestep
        # Set up the probability function once sim's time is initialized
        self.pars.p.set(p=lambda module, sim, uids: np.interp(sim.t.now('year'), self.pars.year, self.pars.rate * sim.t.dt_year))
        return

    def step(self):
        """ Perform the intervention on each timestep. """
        if self.sim.t.now('year') < self.pars.year[0]:
            return

        eligible = ~self.sim.people.alive.asnew()
        for name, disease in self.sim.diseases.items():
            if 'zombie' in name:
                eligible = eligible | (disease.infected & disease.symptomatic)
        death_uids = self.pars.p.filter(eligible.uids)
        self.sim.people.request_death(death_uids)

        return len(death_uids)
    
class zombie_vaccine(ss.Intervention):
    """
    Create a vaccine intervention that affects the probability of infection.
    
    The vaccine can be either "leaky", in which everyone who receives the vaccine 
    receives the same amount of protection (specified by the efficacy parameter) 
    each time they are exposed to an infection. The alternative (leaky=False) is
    that the efficacy is the probability that the vaccine "takes", in which case
    that person is 100% protected (and the remaining people are 0% protected).
    
    Args:
        efficacy (float): efficacy of the vaccine (0<=efficacy<=1)
        leaky (bool): see above
    """
    def __init__(self, efficacy, leaky=True, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.efficacy = efficacy
        self.leaky = leaky

    def administer(self, uids):
        people = self.sim.people
        if self.leaky:
            people.zombie.rel_sus[uids] *= 1 - self.efficacy
        else:
            vaccine_effect = np.random.binomial(1, 1 - self.efficacy, len(uids))
            people.zombie.rel_sus[uids] *= vaccine_effect
        return

    def step(self):
        eligible_uids = self.check_eligibility()
        if len(eligible_uids):
            self.administer(eligible_uids)
        return

    def check_eligibility(self):
        """ Override this method to provide eligibility criteria, defaults to all people. """
        return self.sim.people.auids

class ZombieConnector(ss.Connector):
    """ Connect fast and slow zombies so agents don't become double-zombies """

    def __init__(self, pars=None, **kwargs):
        # Updated to use set_metadata for setting the name and requires to specify required modules
        super().__init__(name='zombie_connector', **kwargs) 

        # Updated to use `define_pars` method from the new structure
        self.define_pars(
            rel_sus=0
        )
        # Update parameters with any additional input arguments
        self.update_pars(pars, **kwargs)
        return

    def step(self):
        """ Specify cross protection between fast and slow zombies """

        # Access the people and disease modules using the updated sim and disease access patterns
        ppl = self.sim.people
        fast = self.sim.diseases['fast_zombie']
        slow = self.sim.diseases['slow_zombie']

        # Implement the logic for modifying the susceptibility based on infection states
        fast.rel_sus[ppl.alive] = 1
        fast.rel_sus[slow.infected] = self.pars.rel_sus

        slow.rel_sus[ppl.alive] = 1
        slow.rel_sus[fast.infected] = self.pars.rel_sus

        return
