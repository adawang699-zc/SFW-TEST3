"""
WebSocket routing for bandwidth test
"""
from django.urls import re_path
from djangoProject.consumers import BandwidthTestConsumer

websocket_urlpatterns = [
    re_path(r'^ws/bandwidth/(?P<test_id>\w+)/$',
            BandwidthTestConsumer.as_asgi()),
]