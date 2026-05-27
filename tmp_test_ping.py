import paramiko as p
c = p.SSHClient()
c.set_missing_host_key_policy(p.AutoAddPolicy())
c.connect("10.40.20.153", 22, "admin", "tdhx@2017", timeout=10)
_, so, _ = c.exec_command("ping -c 2 -W 3 192.168.81.140", timeout=20)
print(so.read().decode(errors="ignore"))
c.close()
