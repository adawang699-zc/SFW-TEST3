"""
WebSocket routing for bandwidth test and port test
"""
from django.urls import re_path
from djangoProject.consumers import BandwidthTestConsumer, PortTestConsumer

websocket_urlpatterns = [
    re_path(r'^ws/bandwidth/(?P<test_id>\w+)/$',
            BandwidthTestConsumer.as_asgi()),
    re_path(r'^ws/port-test/(?P<test_id>\w+)/$',
            PortTestConsumer.as_asgi()),
]