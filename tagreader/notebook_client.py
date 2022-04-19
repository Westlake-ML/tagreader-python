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
            self._connect_btn
        )

    @output.capture()
    def _connect(self, _):
        super().__init__(
            datasource=self.source,
            server=self.server,
            url=self.server,
            imstype=self.imstype,
        )
        self.connect()

    @property
    def imstype(self):
        return self._ims_widget.value

    @property
    def server(self):
        return self._server_widget.value
    
    @property
    def source(self):
        return self._sources_widget.value

    def _update_server(self, _):
        """
        Callback when new IMS type is selected.
        """
        ims = self._ims_widget.value
        self._server_widget.update(ims)
        self._sources_widget.update(ims, self.server)
    
    def _update_sources(self, _):
        """
        Callback when new server is selected.
        """
        self._sources_widget.update(self.imstype, server=self.server)
