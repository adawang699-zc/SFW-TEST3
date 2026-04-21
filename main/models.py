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


class AgentLock(models.Model):
    """
    Agent 租用锁定模型 - 多用户并发使用 Agent 的资源管理

    用户租用 Agent 组后锁定，防止其他用户同时使用
    基于活跃时间自动释放：用户无活动超过指定时间后自动释放
    """
    LOCK_STATUS = [
        ('active', '活跃'),
        ('expired', '已过期'),
        ('released', '已释放'),
    ]

    user_identifier = models.CharField(
        max_length=100,
        verbose_name="用户标识符",
        help_text="用户输入的标识，如 user1、张三"
    )
    client_ip = models.GenericIPAddressField(
        verbose_name="客户端 IP",
        help_text="访问前端的 IP 地址，用于追踪"
    )
    locked_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="租用时间"
    )
    last_activity_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="最后活跃时间",
        help_text="用户最后一次访问 Agent 相关功能的时间"
    )
    expire_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="过期时间",
        help_text="仅用于显示，实际过期基于 last_activity_at"
    )
    status = models.CharField(
        max_length=20,
        choices=LOCK_STATUS,
        default='active',
        verbose_name="状态"
    )
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="释放时间"
    )
    agents = models.ManyToManyField(
        LocalAgent,
        verbose_name="租用的 Agent",
        help_text="被租用的 Agent 组"
    )

    class Meta:
        verbose_name = "Agent 租用"
        verbose_name_plural = "Agent 租用管理"
        ordering = ['-locked_at']

    def __str__(self):
        return f"{self.user_identifier} ({self.client_ip}) - {self.status}"

    def is_expired(self):
        """检查是否已过期（基于活跃时间）"""
        from django.utils import timezone
        INACTIVITY_TIMEOUT_HOURS = 2  # 无活动 2 小时后过期
        timeout = timezone.timedelta(hours=INACTIVITY_TIMEOUT_HOURS)
        return timezone.now() > self.last_activity_at + timeout

    def get_remaining_time(self):
        """获取剩余时间（基于活跃时间，秒）"""
        from django.utils import timezone
        if self.status != 'active':
            return 0
        INACTIVITY_TIMEOUT_HOURS = 2
        timeout = timezone.timedelta(hours=INACTIVITY_TIMEOUT_HOURS)
        remaining = (self.last_activity_at + timeout - timezone.now()).total_seconds()
        return max(0, int(remaining))

    def update_activity(self):
        """更新活跃时间"""
        from django.utils import timezone
        self.last_activity_at = timezone.now()
        self.save(update_fields=['last_activity_at'])


class TestDevice(models.Model):
    """
    测试设备模型 - 防火墙被测设备

    支持长跑环境自动监测、后台密码等功能
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
    password = models.CharField(max_length=200, default='', blank=True, verbose_name="SSH密码", help_text="SSH登录密码")
    backend_password = models.CharField(max_length=200, default='', blank=True, verbose_name="后台密码", help_text="后台root密码，留空则使用设备类型默认密码")
    is_long_running = models.BooleanField(default=False, verbose_name="长跑环境", help_text="长跑环境默认启动监测")
    # 硬件信息（只获取一次）
    hardware_model = models.CharField(max_length=200, default='', blank=True, verbose_name="硬件型号", help_text="通过 show hwtype 获取")
    cpu_model = models.CharField(max_length=200, default='', blank=True, verbose_name="CPU型号", help_text="通过 lscpu 获取 Model name")
    cpu_cores = models.IntegerField(default=0, verbose_name="CPU核数", help_text="通过 lscpu 获取 CPU(s)")
    description = models.TextField(blank=True, null=True, verbose_name="描述信息")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "测试设备"
        verbose_name_plural = "测试设备"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.ip})"


class DeviceAlertStatus(models.Model):
    """设备告警状态模型 - 用于跟踪设备告警和忽略状态"""
    ALERT_TYPES = [
        ('cpu', 'CPU告警'),
        ('memory', '内存告警'),
        ('coredump', 'Coredump告警'),
    ]

    device_id = models.IntegerField(verbose_name="设备ID")
    device_name = models.CharField(max_length=100, verbose_name="设备名称")
    alert_type = models.CharField(max_length=20, choices=ALERT_TYPES, verbose_name="告警类型")
    alert_value = models.FloatField(verbose_name="告警值", help_text="CPU/内存使用率或coredump文件数量")
    has_alert = models.BooleanField(default=True, verbose_name="是否告警")
    is_ignored = models.BooleanField(default=False, verbose_name="是否已忽略")
    ignore_until = models.DateTimeField(null=True, blank=True, verbose_name="忽略截止时间", help_text="忽略后一周内不再提醒")
    alert_time = models.DateTimeField(auto_now_add=True, verbose_name="告警时间")
    last_email_time = models.DateTimeField(null=True, blank=True, verbose_name="最后发送邮件时间")
    email_sent = models.BooleanField(default=False, verbose_name="已发送邮件")

    class Meta:
        verbose_name = "设备告警状态"
        verbose_name_plural = verbose_name
        ordering = ['-alert_time']
        indexes = [
            models.Index(fields=['device_id', 'alert_type']),
            models.Index(fields=['has_alert', 'is_ignored']),
        ]

    def __str__(self):
        return f"{self.device_name} - {self.get_alert_type_display()} ({self.alert_value})"

    def is_ignore_active(self):
        """检查忽略状态是否仍然有效"""
        if not self.is_ignored or not self.ignore_until:
            return False
        from django.utils import timezone
        return timezone.now() < self.ignore_until


class AlertConfig(models.Model):
    """告警配置模型 - 用于存储邮件告警配置"""
    smtp_server = models.CharField(max_length=100, verbose_name="SMTP服务器")
    smtp_port = models.IntegerField(default=587, verbose_name="SMTP端口")
    sender_email = models.CharField(max_length=100, verbose_name="发件人邮箱")
    sender_password = models.CharField(max_length=200, verbose_name="发件人密码")
    use_tls = models.BooleanField(default=True, verbose_name="使用TLS")
    use_ssl = models.BooleanField(default=False, verbose_name="使用SSL")
    recipients = models.TextField(verbose_name="收件人邮箱", help_text="多个邮箱用换行分隔")
    check_interval = models.IntegerField(default=300, verbose_name="监测频率（秒）")
    cpu_threshold = models.IntegerField(default=80, verbose_name="CPU告警阈值（%）")
    memory_threshold = models.IntegerField(default=80, verbose_name="内存告警阈值（%）")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "告警配置"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"告警配置 - {self.smtp_server}"

    def get_recipients_list(self):
        """获取收件人列表"""
        return [r.strip() for r in self.recipients.split('\n') if r.strip() and '@' in r]


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


class DeviceMonitorData(models.Model):
    """
    设备监控数据 - 存储设备的 CPU、内存、磁盘、网络监控数据

    后台定时任务获取数据并存储，前端调用时返回最新值
    """
    device_id = models.IntegerField(verbose_name="设备ID")
    device_name = models.CharField(max_length=100, verbose_name="设备名称")
    device_ip = models.GenericIPAddressField(verbose_name="设备IP")

    # CPU 数据
    cpu_usage = models.FloatField(default=0, verbose_name="CPU使用率(%)")
    cpu_name = models.CharField(max_length=100, default='ARM/x86 Processor', verbose_name="CPU型号")

    # 内存数据
    memory_usage = models.FloatField(default=0, verbose_name="内存使用率(%)")
    memory_used = models.IntegerField(default=0, verbose_name="已用内存(MB)")
    memory_total = models.IntegerField(default=0, verbose_name="总内存(MB)")

    # 磁盘数据
    disk_usage = models.FloatField(default=0, verbose_name="磁盘使用率(%)")
    disk_used = models.FloatField(default=0, verbose_name="已用磁盘(GB)")
    disk_total = models.FloatField(default=0, verbose_name="总磁盘(GB)")

    # 网络数据
    rx_rate = models.IntegerField(default=0, verbose_name="接收速率(B/s)")
    tx_rate = models.IntegerField(default=0, verbose_name="发送速率(B/s)")

    # 状态
    is_online = models.BooleanField(default=False, verbose_name="是否在线")
    last_error = models.TextField(blank=True, null=True, verbose_name="最后错误信息")

    # 时间
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "设备监控数据"
        verbose_name_plural = "设备监控数据"
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['device_id']),
        ]

    def __str__(self):
        return f"{self.device_name} 监控数据"