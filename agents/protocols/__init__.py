"""
Agent 工控协议模块
包含 Modbus、S7、ENIP、DNP3、BACnet、MMS、GOOSE/SV、OPC UA 等协议
"""

from .modbus_client import modbus_client, ModbusClient
from .modbus_server import modbus_server, ModbusServer
from .s7_client import s7_client, S7Client
from .s7_server import s7_server, S7Server

# OPC UA 协议
try:
    from .opcua_common import OPCUA_AVAILABLE, OPCUA_PORT, DEFAULT_VARIABLES
    from .opcua_server import opcua_server, OpcUaServer
    from .opcua_client import opcua_client, OpcUaClient
    from .opcua_gateway import opcua_gateway, OpcUaGatewayHelper
except ImportError as e:
    print(f"警告: OPC UA 模块导入失败: {e}")
    OPCUA_AVAILABLE = False
    opcua_server = None
    opcua_client = None
    opcua_gateway = None
    OpcUaServer = None
    OpcUaClient = None
    OpcUaGatewayHelper = None

__all__ = [
    'modbus_client', 'modbus_server', 's7_client', 's7_server',
    'ModbusClient', 'ModbusServer', 'S7Client', 'S7Server',
    'opcua_server', 'opcua_client', 'opcua_gateway',
    'OpcUaServer', 'OpcUaClient', 'OpcUaGatewayHelper',
    'OPCUA_AVAILABLE', 'OPCUA_PORT',
]