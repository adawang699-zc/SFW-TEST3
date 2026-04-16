from django.contrib import admin
from .models import NetworkInterface, LocalAgent, TestDevice, AgentStatistics


@admin.register(NetworkInterface)
class NetworkInterfaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'ip_address', 'mac_address', 'speed', 'is_management', 'is_available']
    list_filter = ['is_management', 'is_available']
    search_fields = ['name', 'ip_address']


@admin.register(LocalAgent)
class LocalAgentAdmin(admin.ModelAdmin):
    list_display = ['agent_id', 'interface', 'port', 'status', 'auto_start', 'created_at']
    list_filter = ['status', 'auto_start']
    search_fields = ['agent_id']


@admin.register(TestDevice)
class TestDeviceAdmin(admin.ModelAdmin):
    list_display = ['name', 'type', 'ip', 'port', 'created_at']
    list_filter = ['type']
    search_fields = ['name', 'ip']


@admin.register(AgentStatistics)
class AgentStatisticsAdmin(admin.ModelAdmin):
    list_display = ['agent', 'packets_sent', 'packets_received', 'scan_count', 'replay_count']
    search_fields = ['agent__agent_id']