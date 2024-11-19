with open("zombiesim.diff", "r") as f:
  diff_lines = f.readlines()

# HACK: choose which code to refactor and set the output text
# code_file = "../../zombie.py"; result_file = "zombie_copilot.txt"
code_file = "../../simulate_zombies.py"; result_file = "simulate_zombies_copilot.txt"

with open(code_file, "r") as f:
  code_lines = f.readlines()

chat_session = model.start_chat()
parts = ["Here is the diff information for an update to the starsim (ss) package:"] + \
        diff_lines + \
        ["Now please refactor the below code to maintain compatibility with the starsim (ss) code: "] + \
        code_lines
response = chat_session.send_message("\n\n".join(parts))
print(response.text)
with open(result_file, "w") as f:
  f.write(response.text)