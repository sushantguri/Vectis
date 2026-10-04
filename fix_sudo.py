import re
with open('vectis/roles/dns_role.py', 'r') as f:
    content = f.read()
# insert sudo -v before the spinner
new_content = re.sub(
    r'(with ui\.spinner\("Restarting dnsmasq.*?:\n)', 
    r'subprocess.run(["sudo", "-v"], check=True)\n        \1', 
    content, flags=re.DOTALL
)
with open('vectis/roles/dns_role.py', 'w') as f:
    f.write(new_content)
