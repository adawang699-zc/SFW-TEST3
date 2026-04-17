"""
URL Configuration - Ubuntu 多 Agent 一体化部署平台
"""

from django.urls import path
from . import views

app_name = 'main'

urlpatterns = [
    # ========== 页面路由 ==========
    path('', views.home, name='home'),
    path('agent-manage/', views.agent_manage, name='agent_manage'),
    path('device-monitor/', views.device_monitor, name='device_monitor'),
    path('packet-send/', views.packet_send, name='packet_send'),
    path('industrial-protocol/', views.industrial_protocol, name='industrial_protocol'),
    path('port-scan/', views.port_scan, name='port_scan'),
    path('packet-replay/', views.packet_replay, name='packet_replay'),

    # ========== 网卡管理 API ==========
    path('api/interfaces/scan/', views.api_scan_interfaces, name='api_scan_interfaces'),
    path('api/interfaces/list/', views.api_interface_list, name='api_interface_list'),
    path('api/interfaces/config-ip/', views.api_interface_config_ip, name='api_interface_config_ip'),
    path('api/interfaces/startup/', views.api_interface_startup, name='api_interface_startup'),

    # ========== Agent 管理 API ==========
    path('api/agents/list/', views.api_agent_list, name='api_agent_list'),
    path('api/agents/create/', views.api_agent_create, name='api_agent_create'),
    path('api/agents/delete/', views.api_agent_delete, name='api_agent_delete'),
    path('api/agents/start/', views.api_agent_start, name='api_agent_start'),
    path('api/agents/stop/', views.api_agent_stop, name='api_agent_stop'),
    path('api/agents/status/', views.api_agent_status, name='api_agent_status'),
    path('api/agents/logs/', views.api_agent_logs, name='api_agent_logs'),

    # ========== 功能 API（代理到 Agent）==========
    path('api/send_packet/', views.api_send_packet, name='api_send_packet'),
    path('api/send_protocol/', views.api_send_protocol, name='api_send_protocol'),
    path('api/start_scan/', views.api_start_scan, name='api_start_scan'),
    path('api/stop_scan/', views.api_stop_scan, name='api_stop_scan'),
    path('api/scan_progress/', views.api_scan_progress, name='api_scan_progress'),
    path('api/scan_results/', views.api_scan_results, name='api_scan_results'),
    path('api/list_pcap_files/', views.api_list_pcap_files, name='api_list_pcap_files'),
    path('api/start_replay/', views.api_start_replay, name='api_start_replay'),
    path('api/stop_replay/', views.api_stop_replay, name='api_stop_replay'),
    path('api/replay_stats/', views.api_replay_stats, name='api_replay_stats'),

    # ========== 测试设备 API ==========
    path('api/devices/list/', views.api_device_list, name='api_device_list'),
    path('api/devices/add/', views.api_device_add, name='api_device_add'),
    path('api/devices/update/', views.api_device_update, name='api_device_update'),
    path('api/devices/delete/', views.api_device_delete, name='api_device_delete'),
    path('api/device/test_connection/', views.api_device_test_connection, name='api_device_test_connection'),
    path('api/device/monitor_data/', views.api_device_monitor_data, name='api_device_monitor_data'),
    path('api/device/disk_data/', views.api_device_disk_data, name='api_device_disk_data'),
    path('api/device/execute/', views.api_device_execute, name='api_device_execute'),
    path('api/device/monitoring/toggle/', views.api_device_monitoring_toggle, name='api_device_monitoring_toggle'),
    path('api/device/monitoring/status/', views.api_device_monitoring_status, name='api_device_monitoring_status'),
    path('api/device/alert_config/', views.api_device_alert_config, name='api_device_alert_config'),
    path('api/device/alert_config/test/', views.api_device_alert_config_test, name='api_device_alert_config_test'),
    path('api/device/alert_status/', views.api_device_alert_status, name='api_device_alert_status'),
    path('api/device/alert_ignore/', views.api_device_alert_ignore, name='api_device_alert_ignore'),
    path('api/device/coredump_list/', views.api_device_coredump_list, name='api_device_coredump_list'),

    # ========== 系统信息 API ==========
    path('api/system/info/', views.api_system_info, name='api_system_info'),

    # ========== Syslog 接收 ==========
    path('syslog-receiver/', views.syslog_receiver, name='syslog_receiver'),
    path('api/syslog/status/', views.api_syslog_status, name='api_syslog_status'),
    path('api/syslog/control/', views.api_syslog_control, name='api_syslog_control'),
    path('api/syslog/logs/', views.api_syslog_logs, name='api_syslog_logs'),
    path('api/syslog/clear/', views.api_syslog_clear, name='api_syslog_clear'),
    path('api/syslog/filter/', views.api_syslog_filter, name='api_syslog_filter'),

    # ========== SNMP 管理 ==========
    path('snmp/', views.snmp, name='snmp'),
    path('api/snmp/get/', views.api_snmp_get, name='api_snmp_get'),
    path('api/snmp/trap/control/', views.api_snmp_trap_control, name='api_snmp_trap_control'),
    path('api/snmp/trap/status/', views.api_snmp_trap_status, name='api_snmp_trap_status'),
    path('api/snmp/trap/traps/', views.api_snmp_trap_traps, name='api_snmp_trap_traps'),
    path('api/snmp/trap/clear/', views.api_snmp_trap_clear, name='api_snmp_trap_clear'),

    # ========== DHCP 客户端 ==========
    path('dhcp-client/', views.dhcp_client, name='dhcp_client'),

    # ========== 知识库管理 ==========
    path('knowledge-base/', views.knowledge_base, name='knowledge_base'),
    path('api/knowledge/templates/', views.api_knowledge_templates, name='api_knowledge_templates'),
    path('api/knowledge/templates/<str:name>/', views.api_knowledge_template_get, name='api_knowledge_template_get'),
    path('api/knowledge/templates/save/', views.api_knowledge_template_save, name='api_knowledge_template_save'),
    path('api/knowledge/templates/delete/', views.api_knowledge_template_delete, name='api_knowledge_template_delete'),
    path('api/knowledge/create/', views.api_knowledge_create, name='api_knowledge_create'),
    path('api/knowledge/upgrade/', views.api_knowledge_upgrade, name='api_knowledge_upgrade'),

    # ========== 漏洞库 ==========
    path('api/knowledge/vul/templates/', views.api_vul_templates, name='api_vul_templates'),
    path('api/knowledge/vul/templates/<str:name>/', views.api_vul_template_get, name='api_vul_template_get'),
    path('api/knowledge/vul/templates/save/', views.api_vul_template_save, name='api_vul_template_save'),
    path('api/knowledge/vul/templates/delete/', views.api_vul_template_delete, name='api_vul_template_delete'),
    path('api/knowledge/vul/create/', views.api_vul_create, name='api_vul_create'),
    path('api/knowledge/vul/upgrade/', views.api_vul_upgrade, name='api_vul_upgrade'),

    # ========== 病毒库 ==========
    path('api/knowledge/virus/templates/', views.api_virus_templates, name='api_virus_templates'),
    path('api/knowledge/virus/templates/<str:name>/', views.api_virus_template_get, name='api_virus_template_get'),
    path('api/knowledge/virus/templates/save/', views.api_virus_template_save, name='api_virus_template_save'),
    path('api/knowledge/virus/templates/delete/', views.api_virus_template_delete, name='api_virus_template_delete'),
    path('api/knowledge/virus/create/', views.api_virus_create, name='api_virus_create'),
    path('api/knowledge/virus/upgrade/', views.api_virus_upgrade, name='api_virus_upgrade'),

    # ========== 授权管理 ==========
    path('license-management/', views.license_management, name='license_management'),
    path('api/license/verify-password/', views.api_license_verify_password, name='api_license_verify_password'),
    path('api/license/knowledge/generate/', views.api_license_knowledge_generate, name='api_license_knowledge_generate'),
    path('api/license/knowledge/decrypt/', views.api_license_knowledge_decrypt, name='api_license_knowledge_decrypt'),
    path('api/license/device/test_connection/', views.api_license_device_test_connection, name='api_license_device_test_connection'),
    path('api/license/device/generate/', views.api_license_device_generate, name='api_license_device_generate'),
]