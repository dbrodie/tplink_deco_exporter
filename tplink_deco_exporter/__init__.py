"""
TP-Link Deco Prometheus Exporter

This module provides a Prometheus exporter for TP-Link Deco devices.
"""

from .api import TplinkDecoApi
from .prometheus_metrics import DecoMetricsCollector

__all__ = ['TplinkDecoApi', 'DecoMetricsCollector']
