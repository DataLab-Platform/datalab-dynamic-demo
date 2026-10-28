"""Contract tests for the generated DataLab plugin."""

from datalab.plugins import PluginCapability

from datalab_dynamic_demo.adapters import desktop


def test_plugin_descriptor() -> None:
    """The generated entry point exposes stable SDK metadata."""
    plugin_class = desktop.DynamicModeDemoPlugin
    assert plugin_class.get_plugin_id() == "org.datalab.dynamic-demo"
    assert plugin_class.PLUGIN_INFO.version == "0.1.0"
    assert plugin_class.PLUGIN_INFO.capabilities == frozenset(
        {
            PluginCapability.APPLICATION,
        }
    )
