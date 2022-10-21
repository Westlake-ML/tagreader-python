import ipywidgets as widgets
from IPython.display import display

from .clients import IMSClient
from .widgets import IMSWidget, ServerWidget, SourcesWidget

class NotebookClient(IMSClient):

    output = widgets.Output()

    def __init__(self):
        self._ims_widget = IMSWidget()
        self._server_widget = ServerWidget(imstype=self.imstype)
        self._sources_widget = SourcesWidget(imstype=self.imstype, server=self.server)
        self._connect_btn = widgets.Button(description="Connect")
        
        self._ims_widget.observe(self._update_server, names="value")
        self._server_widget.observe(self._update_sources, names="value")
        self._connect_btn.on_click(self._connect)

        display(
            self._ims_widget,
            self._server_widget,
            self._sources_widget,
            self._connect_btn,
        )

    @property
    def imstype(self):
        return self._ims_widget.value

    @property
    def server(self):
        return self._server_widget.value
    
    @property
    def source(self):
        return self._sources_widget.value

    def _update_server(self, ims_change):
        """
        Callback when new IMS type is selected.
        """

        new_ims = ims_change["new"]
        self._server_widget.update(new_ims)
    
    def _update_sources(self, server_change):
        """
        Callback when new server is selected.
        """

        new_server = server_change["new"]
        self._sources_widget.update(self.imstype, new_server)
    
    def _connect(self, _):
        """
        Callback when 'Connect' button is pressed.
        """

        # Server widget is dual-purpose as 'server' and as 'url'.
        # Downstream handlers will sort out which paramaters they need based on imstype.
        super().__init__(
            datasource=self.source,
            server=self.server,
            url=self.server,
            imstype=self.imstype,
        )
        self.connect()
