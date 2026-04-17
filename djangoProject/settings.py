"""
Django settings for ubuntu_deploy project.

Ubuntu 多 Agent 一体化部署平台
"""

from pathlib import Path
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',') if os.environ.get('ALLOWED_HOSTS') else ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'main',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'djangoProject.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'djangoProject.wsgi.application'

# Database - SQLite3 for single host deployment
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Default auto field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========== Agent 配置 ==========

# 管理网卡名称（部署时配置，如 eth0）
# 这个网卡用于 Django Web 服务，不参与 Agent 绑定
MANAGEMENT_INTERFACE = os.environ.get('MANAGEMENT_INTERFACE', 'eth0')

# Agent 默认端口范围
AGENT_PORT_RANGE_START = int(os.environ.get('AGENT_PORT_START', '8888'))

# Agent 工作目录
AGENT_WORK_DIR = os.environ.get('AGENT_WORK_DIR', '/opt/SFW-TEST3')

# Agent Python 虚拟环境路径
AGENT_VENV_PYTHON = os.environ.get('AGENT_VENV_PYTHON', '/opt/SFW-TEST3/sfw/bin/python')

# ========== 日志配置 ==========

LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '[{asctime}] {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
            'level': 'INFO',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'django.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
        'agent_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'agent.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
        # Agent 模块独立日志 handlers
        'agent_packet_capture': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'agent_packet_capture.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
        'agent_port_scanner': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'agent_port_scanner.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
        'agent_packet_sender': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'agent_packet_sender.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
        'agent_packet_replay': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'agent_packet_replay.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
        'agent_mail_service': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'agent_mail_service.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
        'agent_dhcp_client': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'agent_dhcp_client.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'main': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'agents': {
            'handlers': ['console', 'agent_file'],
            'level': 'INFO',
            'propagate': False,
        },
        # Agent 模块独立日志
        'agents.packet_capture': {
            'handlers': ['console', 'agent_packet_capture'],
            'level': 'INFO',
            'propagate': False,
        },
        'agents.port_scanner': {
            'handlers': ['console', 'agent_port_scanner'],
            'level': 'INFO',
            'propagate': False,
        },
        'agents.packet_sender': {
            'handlers': ['console', 'agent_packet_sender'],
            'level': 'INFO',
            'propagate': False,
        },
        'agents.packet_replay': {
            'handlers': ['console', 'agent_packet_replay'],
            'level': 'INFO',
            'propagate': False,
        },
        'agents.mail_service': {
            'handlers': ['console', 'agent_mail_service'],
            'level': 'INFO',
            'propagate': False,
        },
        'agents.dhcp_client': {
            'handlers': ['console', 'agent_dhcp_client'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}