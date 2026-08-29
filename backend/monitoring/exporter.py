from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MetricsExporter:
    """
    Prometheus metrics exporter utility for Voice-Assisted-Coding-Agent.
    """

    def __init__(self, port: int = 9090):
        self.port = port
        self._started = False

    def start(self):
        try:
            from prometheus_client import start_http_server
            start_http_server(self.port)
            self._started = True
            logger.info(f"Metrics exporter started on port {self.port}")
        except Exception as e:
            logger.warning(f"Could not start Prometheus metrics exporter: {e}")
