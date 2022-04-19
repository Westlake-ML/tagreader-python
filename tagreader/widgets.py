import ipywidgets as widgets
from ipywidgets import Dropdown

from .clients import list_sources
from .odbc_handlers import list_adsa_servers
from .web_handlers import URLs

class IMSWidget(Dropdown):
    
    def __init__(self):
        super().__init__(
            options=["ip21", "aspenone"],
            value="ip21",
            description="IMS Type",
        )

class ServerWidget(Dropdown):

    def __init__(self, imstype: str):
        super().__init__()
        self.update(imstype)
    
    def update(self, imstype: str):
        if imstype == "ip21":
            self.options = list_adsa_servers()
            self.description = "ADSA Servers"
        elif imstype == "aspenone":
            self.options = URLs.all()
            self.description = "URLs"
        else:
            self.options = [None]
            self.description = "Servers"
        self.value = self.options[0]

class SourcesWidget(Dropdown):

    def __init__(self, imstype: str, server: str = None):
        super().__init__()
        self.update(imstype, server=server)

    def update(self, imstype: str, server: str = None):
        self.sources = list_sources(imstype=imstype, server=server, url=server)
        self.options = self.sources
        self.value = self.sources[0]
        self.description = "Sources"
