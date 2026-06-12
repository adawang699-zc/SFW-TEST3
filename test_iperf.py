#!/usr/bin/env python3
"""Test iperf3 stdout reading in namespace"""
import subprocess, re, time, threading

stats = {'running': False, 'instant_bps': 0}

def reader(proc):
    interval_re = re.compile(
        r'\[\s*\d+\]\s+[\d.]+-[\d.]+\s+sec\s+([\d.]+)\s+'
        r'(KBytes|MBytes|GBytes)\s+([\d.]+)\s+'
        r'(Kbits/sec|Mbits/sec|Gbits/sec)'
    )
    stats['running'] = True
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        m = interval_re.search(line)
        if m:
            speed_val = float(m.group(3))
            speed_unit = m.group(4)
            if speed_unit == 'Kbits/sec':
                speed_mbps = speed_val / 1000
            elif speed_unit == 'Gbits/sec':
                speed_mbps = speed_val * 1000
            else:
                speed_mbps = speed_val
            stats['instant_bps'] = speed_mbps
            print(f"PARSED: {speed_mbps:.1f} Mbps from line: {line[:60]}")
    stats['running'] = False
    print("Reader exited")

# Start server
server = subprocess.Popen(['iperf3', '-s', '-p', '5203'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
t_server = threading.Thread(target=reader, args=(server,), daemon=True)
t_server.start()
print(f"Server PID: {server.pid}")
time.sleep(1)

# Start client
client = subprocess.Popen(['iperf3', '-c', '192.168.11.100', '-p', '5203', '-t', '3', '-l', '1400'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
t_client = threading.Thread(target=reader, args=(client,), daemon=True)
t_client.start()
print(f"Client PID: {client.pid}")

time.sleep(5)

print(f"\nFinal stats: {stats}")

server.terminate()
client.terminate()
server.wait(timeout=3)
client.wait(timeout=3)

# Show what we got from stderr
print(f"\nClient stderr: {client.stderr.read()[:200]}")
print("DONE")
