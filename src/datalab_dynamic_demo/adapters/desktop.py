"""DataLab Desktop plugin adapter."""

from __future__ import annotations

from datalab.plugins import PluginBase, PluginCapability, PluginInfo

from .. import PLUGIN_DESCRIPTION, PLUGIN_ID, PLUGIN_NAME, __version__
from ..workflow import RECIPES as WORKFLOW_RECIPES
from .controller import LiveController
from .widgets import LiveStatusWidget, run_command


class DynamicModeDemoPlugin(PluginBase):
    """Mockup of the dynamic mode: live source and auto-updated result."""

    PLUGIN_INFO = PluginInfo(
        id=PLUGIN_ID,
        name=PLUGIN_NAME,
        version=__version__,
        description=PLUGIN_DESCRIPTION,
        capabilities=(PluginCapability.APPLICATION,),
    )
    RECIPES = WORKFLOW_RECIPES

    def __init__(self) -> None:
        super().__init__()
        self.controller: LiveController | None = None
        self.status_widget: LiveStatusWidget | None = None

    def get_controller(self) -> LiveController:
        """Return the live update controller, created on first use."""
        if self.controller is None:
            self.controller = LiveController(self.main)
            self.controller.SIG_SOURCE_REMOVED.connect(self.remove_status_widget)
        return self.controller

    def create_source(self) -> None:
        """Create the simulated source and show the live update indicator."""
        self.get_controller().create_source()
        if self.status_widget is None:
            self.status_widget = LiveStatusWidget(self.controller, self.main)
            self.main.statusBar().addPermanentWidget(self.status_widget)
        self.status_widget.show()

    def follow_result(self) -> None:
        """Follow the selected Gaussian filter result of the source."""
        obj = self.signalpanel.objview.get_current_object()
        if obj is None:
            self.show_warning("Select a Gaussian filter result of the source.")
            return
        run_command(self.main, lambda: self.get_controller().follow(obj))

    def start(self) -> None:
        """Start the live update."""
        run_command(self.main, self.get_controller().start)

    def pause(self) -> None:
        """Pause the live update."""
        run_command(self.main, self.get_controller().pause)

    def resume(self) -> None:
        """Resume the live update."""
        run_command(self.main, self.get_controller().resume)

    def stop(self) -> None:
        """Stop the live update."""
        run_command(self.main, self.get_controller().stop)

    def remove_status_widget(self) -> None:
        """Remove the live update indicator from the status bar."""
        if self.status_widget is not None:
            self.status_widget.close_popup()
            self.main.statusBar().removeWidget(self.status_widget)
            self.status_widget.deleteLater()
            self.status_widget = None

    def create_actions(self) -> None:
        """Create the actions of the Plugins menu."""
        acth = self.signalpanel.acthandler
        with acth.new_menu(self.PLUGIN_INFO.name):
            acth.new_action(
                "Create simulated source",
                triggered=self.create_source,
                select_condition="always",
            )
            acth.new_action(
                "Follow this result",
                triggered=self.follow_result,
                select_condition="exactly_one",
                tip="Follow the selected Gaussian filter result of the source",
            )
            for title, slot, separator in (
                ("Start live update", self.start, True),
                ("Pause live update", self.pause, False),
                ("Resume live update", self.resume, False),
                ("Stop live update", self.stop, False),
            ):
                acth.new_action(
                    title,
                    triggered=slot,
                    select_condition="always",
                    separator=separator,
                )

    def unregister_hooks(self) -> None:
        """Release the live update resources and remove its indicator."""
        if self.controller is not None:
            self.controller.shutdown()
            self.controller.deleteLater()
            self.controller = None
        self.remove_status_widget()
