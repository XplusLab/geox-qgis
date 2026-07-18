"""GEOX Bridge - QGIS plugin entry point."""


def classFactory(iface):
    from .plugin import GeoxBridgePlugin
    return GeoxBridgePlugin(iface)
