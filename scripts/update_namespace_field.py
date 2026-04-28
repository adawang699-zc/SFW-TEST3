#!/usr/bin/env python
"""更新 NetworkInterface namespace 字段"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sfw_test.settings')
django.setup()

from main.models import NetworkInterface

# 更新 eth1 和 eth2 的 namespace
eth1 = NetworkInterface.objects.get(name='eth1')
eth1.namespace = 'ns-eth1'
eth1.save()
print(f'Updated eth1: namespace={eth1.namespace}')

eth2 = NetworkInterface.objects.get(name='eth2')
eth2.namespace = 'ns-eth2'
eth2.save()
print(f'Updated eth2: namespace={eth2.namespace}')

# 验证所有网卡
for iface in NetworkInterface.objects.all():
    if iface.namespace:
        print(f'{iface.name}: namespace={iface.namespace}')