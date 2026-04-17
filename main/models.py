"""
数据库模型 - Ubuntu 多 Agent 一体化部署平台
"""

from django.db import models
from django.conf import settings


class NetworkInterface(models.Model):
    """
    网卡模型 - 存储系统网卡信息

    系统启动时自动扫描，管理网卡（MANAGEMENT_INTERFACE）不参与 Agent 绑定
    """
    name = models.CharField(max_length=50, unique=True, verbose_name="网卡名称")
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name="IP地址")
    mac_address = models.CharField(max_length=17, blank=True, default='', verbose_name="MAC地址")
    speed = models.IntegerField(null=True, blank=True, verbose_name="速率(Mbps)")
    is_management = models.BooleanField(default=False, verbose_name="是否管理网卡")
    is_available = models.BooleanField(default=True, verbose_name="是否可用")
    is_up = models.BooleanField(default=False, verbose_name="是否启动")
    status = models.CharField(max_length=10, default='DOWN', verbose_name="状态")
    detected_at = models.DateTimeField(auto_now_add=True, verbose_name="检测时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "网卡"
        verbose_name_plural = "网卡管理"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.ip_address})"

    def has_agent(self):
        """检查网卡是否已绑定 Agent"""
        return LocalAgent.objects.filter(interface=self).exists()


class LocalAgent(models.Model):
    """
    本地 Agent 模型 - 存储网卡-Agent 绑定关系

    Agent ID 格式: agent_{网卡名}，如 agent_eth1
    每个 Agent 都拥有全功能（报文发送、工控协议、端口扫描、报文回放）
    """
    AGENT_STATUS = [
        ('running', '运行中'),
        ('stopped', '已停止'),
        ('error', '异常'),
        ('unknown', '未知'),
    ]

    agent_id = models.CharField(max_length=50, unique=True, verbose_name="Agent ID")
    interface = models.OneToOneField(
        NetworkInterface,
        on_delete=models.CASCADE,
        verbose_name="绑定网卡"
    )
    port = models.IntegerField(default=8888, verbose_name="监听端口")
    status = models.CharField(
        max_length=20,
        choices=AGENT_STATUS,
        default='stopped',
        verbose_name="状态"
    )
    auto_start = models.BooleanField(default=False, verbose_name="开机自启")
    last_start_time = models.DateTimeField(null=True, blank=True, verbose_name="最后启动时间")
    last_stop_time = models.DateTimeField(null=True, blank=True, verbose_name="最后停止时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "本地 Agent"
        verbose_name_plural = "Agent 管理"
        ordering = ['agent_id']

    def __str__(self):
        return f"{self.agent_id} ({self.interface.name})"

    def get_url(self):
        """获取 Agent HTTP URL"""
        return f"http://{self.interface.ip_address}:{self.port}"

    def get_service_name(self):
        """获取 systemd 服务名称"""
        return f"agent-{self.interface.name}"

    def generate_agent_id(self):
        """生成 Agent ID（基于网卡名）"""
        return f"agent_{self.interface.name}"


class TestDevice(models.Model):
    """
    测试设备模型 - 防火墙被测设备

    与原有功能保持一致，无需改动
    """
    DEVICE_TYPES = [
        ('security_device', '安全设备'),
        ('ic_firewall', '工控防火墙'),
        ('ic_audit', '工控审计'),
        ('ids', 'IDS'),
        ('other', '其他')
    ]

    name = models.CharField(max_length=100, verbose_name="设备名称")
    type = models.CharField(
        max_length=30,
        choices=DEVICE_TYPES,
        default='ic_firewall',
        verbose_name="设备类型"
    )
    ip = models.GenericIPAddressField(verbose_name="IP地址")
    port = models.IntegerField(default=22, verbose_name="管理端口")
    user = models.CharField(max_length=100, default='admin', verbose_name="用户名")
    password = models.CharField(max_length=200, default='', blank=True, verbose_name="密码")
    description = models.TextField(blank=True, null=True, verbose_name="描述信息")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "测试设备"
        verbose_name_plural = "测试设备"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.ip})"


class AgentStatistics(models.Model):
    """
    Agent 统计数据 - 记录 Agent 运行统计

    用于监控 Agent 的发送/接收统计
    """
    agent = models.ForeignKey(LocalAgent, on_delete=models.CASCADE, verbose_name="Agent")
    packets_sent = models.BigIntegerField(default=0, verbose_name="已发送报文数")
    packets_received = models.BigIntegerField(default=0, verbose_name="已接收报文数")
    scan_count = models.IntegerField(default=0, verbose_name="扫描次数")
    replay_count = models.IntegerField(default=0, verbose_name="回放次数")
    last_activity = models.DateTimeField(null=True, blank=True, verbose_name="最后活动时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "Agent 统计"
        verbose_name_plural = "Agent 统计"
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.agent.agent_id} 统计"