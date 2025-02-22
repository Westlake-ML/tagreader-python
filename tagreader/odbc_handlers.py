import os
import pyodbc
import pandas as pd
import numpy as np
import warnings
from typing import List, Optional, Union
from .utils import logging, winreg, find_registry_key, list_subkeys, ReaderType

logging.basicConfig(
    format=" %(asctime)s %(levelname)s: %(message)s", level=logging.INFO
)

ADSA_REGISTRY_PATH = r"Software\AspenTech\ADSA\Caches\\"
server_registry_path = lambda server: ADSA_REGISTRY_PATH + server + r"\\" + os.getlogin()


def list_adsa_servers() -> List[str]:
    try:
        adsa_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ADSA_REGISTRY_PATH)
    except:
        return []
    return list_subkeys(adsa_key)

def validated_server(server: Optional[str] = None) -> str:
    """
    Validates the server, if provided, or returns the first available ADSA server.
    """
    available_servers = list_adsa_servers()

    if server is None:
        if available_servers:
            return available_servers[0]
        else:
            raise FileNotFoundError("No available ADSA servers")
    else:
        assert server in available_servers
        return server

def list_aspen_sources(server: str = None) -> List[str]:
    server = validated_server(server)    
    reg_adsa = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        server_registry_path(server),
    )
    return list_subkeys(reg_adsa)

def list_pi_sources():
    source_list = []
    reg_key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\PISystem\PI-SDK"
    )
    reg_key = find_registry_key(reg_key, "ServerHandles")
    if reg_key is None:
        reg_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\PISystem\PI-SDK")
        reg_key = find_registry_key(reg_key, "ServerHandles")
    if reg_key is not None:
        num_sources, _, _ = winreg.QueryInfoKey(reg_key)
        for i in range(0, num_sources):
            source_list.append(winreg.EnumKey(reg_key, i))
    return source_list


class AspenHandlerODBC:
    def __init__(self, host=None, port=None, options={}):
        self.host = host
        self.port = port
        self.conn = None
        self.cursor = None
        self._max_rows = options.get("max_rows", 100000)
        self._connection_string = options.get("connection_string", None)

    def generate_connection_string(self):
        if self._connection_string is None:
            return f"DRIVER={{AspenTech SQLPlus}};HOST={self.host};PORT={self.port};READONLY=Y;MAXROWS={self._max_rows}"  # noqa E501
        else:
            return self._connection_string

    @staticmethod
    def generate_read_query(
        tag, mapdef, start_time, stop_time, sample_time, read_type, get_status=False
    ):
        if mapdef is None:
            mapdef = {}
        if read_type in [
            ReaderType.COUNT,
            ReaderType.GOOD,
            ReaderType.BAD,
            ReaderType.TOTAL,
            ReaderType.SUM,
            ReaderType.SHAPEPRESERVING,
        ]:
            raise NotImplementedError

        # TODO: How to interpret ip_input_quality and ip_value_quality
        # which use a different numeric schema (e.g. -1) than status from
        # history table (0, 1, 2, 4, 5, 6)
        if get_status and read_type == ReaderType.SNAPSHOT:
            raise NotImplementedError

        if read_type == ReaderType.SNAPSHOT and stop_time is not None:
            raise NotImplementedError(
                "Timestamp not supported for IP.21 ODBC connection using 'SNAPSHOT'. "
                "Try the web API 'aspenone' instead."
            )

        seconds = 0
        if read_type != ReaderType.SNAPSHOT:
            start_time = start_time.tz_convert("UTC")
            stop_time = stop_time.tz_convert("UTC")
            seconds = int(sample_time.total_seconds())
            if read_type == ReaderType.SAMPLED:
                seconds = 0
            else:
                if seconds <= 0:
                    raise NotImplementedError
                    # sample_time = (stop_time-start_time).totalseconds

        timecast_format_query = "%Y-%m-%dT%H:%M:%SZ"  # 05-jan-18 14:00:00

        request_num = {
            ReaderType.SAMPLED: 4,  # VALUES request (actual recorded data), history  # noqa: E501
            ReaderType.SHAPEPRESERVING: 3,  # FITS request, history
            ReaderType.INT: 7,  # TIMES2_EXTENDED request, history
            ReaderType.COUNT: 0,  # Actual data points are used, aggregates
            ReaderType.GOOD: 0,  # Actual data points are used, aggregates
            ReaderType.TOTAL: 0,  # Actual data points are used, aggregates
            ReaderType.NOTGOOD: 0,  # Actual data points are used, aggregates
            ReaderType.SNAPSHOT: None,
        }.get(
            read_type, 1
        )  # Default 1 for aggregates table

        from_column = {
            ReaderType.SAMPLED: "history",
            ReaderType.SHAPEPRESERVING: "history",
            ReaderType.INT: "history",
            ReaderType.SNAPSHOT: '"' + str(tag) + '"',
        }.get(read_type, "aggregates")
        # For RAW: historyevent?
        # Ref https://help.sap.com/saphelp_pco151/helpdata/en/4c/72e34ee631469ee10000000a15822d/content.htm?no_cache=true  # noqa: E501

        ts = "ts"
        if from_column == "aggregates":
            ts = "ts_start"
        elif read_type == ReaderType.SNAPSHOT:
            ts = mapdef.get("MAP_CurrentTimeStamp", "IP_INPUT_TIME")

        value = {
            ReaderType.MIN: "min",
            ReaderType.MAX: "max",
            ReaderType.RNG: "rng",
            ReaderType.AVG: "avg",
            ReaderType.VAR: "var",
            ReaderType.STD: "std",
            ReaderType.GOOD: "good",
            ReaderType.NOTGOOD: "ng",
            ReaderType.TOTAL: "sum",
            ReaderType.SUM: "sum",
            ReaderType.SNAPSHOT: mapdef.get("MAP_CurrentValue", "IP_INPUT_VALUE"),
        }.get(read_type, "value")

        status = {
            ReaderType.SNAPSHOT: mapdef.get("MAP_CurrentQuality", "IP_INPUT_QUALITY"),
        }.get(read_type, "status")

        query = [f'SELECT ISO8601({ts}) AS "time", {value} AS "value"']
        if get_status:
            # status is returned/interpreted as char regardless if cast to INT
            query.extend([f', {status} AS "status"'])
        query.extend([f"FROM {from_column}"])

        if ReaderType.SNAPSHOT != read_type:
            start = start_time.strftime(timecast_format_query)
            stop = stop_time.strftime(timecast_format_query)
            query.extend([f"WHERE name = {tag!r}"])
            if mapdef:
                query.extend([f'AND FIELD_ID = FT({mapdef["MAP_HistoryValue"]!r})'])
            if ReaderType.RAW != read_type:
                query.extend([f"AND (period = {seconds*10})"])
            query.extend(
                [
                    f"AND (request = {request_num})",
                    f"AND (ts BETWEEN {start!r} AND {stop!r})",
                    "ORDER BY ts",
                ]
            )

        return " ".join(query)

    def set_options(self, options):
        pass

    def connect(self):
        connection_string = self.generate_connection_string()
        # The default autocommit=False is not supported by PI odbc driver.
        self.conn = pyodbc.connect(connection_string, autocommit=True)
        self.cursor = self.conn.cursor()

    @staticmethod
    def _generate_query_get_mapdef_for_search(tag):
        query = [
            "SELECT DISTINCT a.name as tagname, m.NAME, m.MAP_DefinitionRecord,",
            "m.MAP_IsDefault, m.MAP_Description, m.MAP_Units, m.MAP_Base, m.MAP_Range",
            "FROM all_records a",
            "LEFT JOIN atmapdef m ON a.definition = m.MAP_DefinitionRecord",
            "WHERE a.name",
        ]
        if "%" in tag:
            query.append(f"LIKE '{tag}'")
        else:
            query.append(f"='{tag}'")
        query.append("AND m.MAP_IsDefault = 'TRUE'")
        return " ".join(query)

    def _get_mapdef_for_search(self, tag):
        query = self._generate_query_get_mapdef_for_search(tag)
        self.cursor.execute(query)
        colnames = [col[0] for col in self.cursor.description]
        rows = self.cursor.fetchall()
        temp = [dict(zip(colnames, row)) for row in rows]
        mapdef = {}
        for t in temp:
            if t["tagname"] not in mapdef:
                mapdef[t["tagname"]] = [t]
            else:
                mapdef[t["tagname"]].append(t)
        return mapdef

    @staticmethod
    def _generate_query_search_tag(mapdef, desc=None):
        if mapdef["MAP_DefinitionRecord"] is None:
            return None
        query = [
            f"SELECT \"{mapdef['MAP_Description']}\"",
            f"FROM {mapdef['MAP_DefinitionRecord']}",
            f"WHERE name = '{mapdef['tagname']}'",
        ]
        if desc is not None:
            query.append(f"AND {mapdef['MAP_Description']} like '{desc}'")
        return " ".join(query)

    @staticmethod
    def _generate_query_get_mapdefs(tag):
        query = [
            "SELECT m.NAME, m.MAP_DefinitionRecord, m.MAP_IsDefault,",
            "m.MAP_Description, m.MAP_Units, m.MAP_Base, m.MAP_Range,",
            "m.MAP_CurrentValue, m.MAP_CurrentTimeStamp, m.MAP_CurrentQuality,",
            f'm.MAP_HistoryValue FROM "{tag}" t',
            "LEFT JOIN atmapdef m ON t.definition = m.MAP_DefinitionRecord",
        ]
        return " ".join(query)

    def _get_mapdefs(self, tag):
        query = self._generate_query_get_mapdefs(tag)
        self.cursor.execute(query)
        colnames = [col[0] for col in self.cursor.description]
        rows = self.cursor.fetchall()
        mapdefs = [dict(zip(colnames, row)) for row in rows]
        return mapdefs

    def _get_specific_mapdef(self, tagname, mapping):
        mapdefs = self._get_mapdefs(tagname)
        for mapdef in mapdefs:
            if mapdef["NAME"].lower() == mapping.lower():
                return mapdef
        return None

    def _get_default_mapdef(self, tagname):
        mapdefs = self._get_mapdefs(tagname)
        for mapdef in mapdefs:
            if mapdef["MAP_IsDefault"] == "TRUE":
                return mapdef
        return None

    def search(self, tag=None, desc=None):
        if tag is None:
            raise ValueError("Tag is a required argument")

        tag = tag.replace("*", "%") if isinstance(tag, str) else None
        desc = desc.replace("*", "%") if isinstance(desc, str) else None

        mapdef = self._get_mapdef_for_search(tag)

        res = []
        for tagname in mapdef:  # TODO: Refactor
            query = self._generate_query_search_tag(mapdef[tagname][0], desc)
            # Ignore records with no associated mapping.
            if query is None:
                continue
            self.cursor.execute(query)
            r = self.cursor.fetchone()
            # No matches
            if r is None:
                continue
            description = ""
            # Match, but no value for description
            if r[0] is not None:
                description = r[0]
            res.append((tagname, description))
        res = list(set(res))
        return res

    def _get_tag_metadata(self, tag):
        return None

    def _get_tag_unit(self, tag):
        (tagname, mapping) = tag.split(";") if ";" in tag else (tag, None)
        if mapping is None:
            mapdef = self._get_default_mapdef(tagname)
        else:
            mapdef = self._get_specific_mapdef(tagname, mapping)
        query = f"SELECT name, \"{mapdef['MAP_Units']}\" as engunit FROM \"{tagname}\""
        self.cursor.execute(query)
        res = self.cursor.fetchone()
        if not res.engunit:
            res.engunit = ""
        return res.engunit

    def _get_tag_description(self, tag):
        (tagname, mapping) = tag.split(";") if ";" in tag else (tag, None)
        if mapping is None:
            mapping = self._get_default_mapdef(tagname)
        else:
            mapping = self._get_specific_mapdef(tagname, mapping)
        query = [
            f"SELECT name, \"{mapping['MAP_Description']}\" as description",
            f' FROM "{tagname}"',
        ]
        query = " ".join(query)
        self.cursor.execute(query)
        res = self.cursor.fetchone()
        if not res.description:
            res.description = ""
        return res.description

    def read_tag(
        self,
        tag,
        start_time,
        stop_time,
        sample_time,
        read_type,
        metadata=None,
        get_status=False,
    ):
        # if get_status:
        #     raise NotImplementedError
        (cleantag, mapping) = tag.split(";") if ";" in tag else (tag, None)
        mapdef = dict()
        if mapping is not None:
            mapdef = self._get_specific_mapdef(cleantag, mapping)
        query = self.generate_read_query(
            cleantag, mapdef, start_time, stop_time, sample_time, read_type, get_status
        )
        # logging.debug(f'Executing SQL query {query!r}')
        df = pd.read_sql(
            query,
            self.conn,
            index_col="time",
            parse_dates={"time": "%Y-%m-%dT%H:%M:%S.%fZ"},
        ).fillna(value=np.nan)
        # This warning will trigger also for (at least some) valid tags with no data.
        # if len(df.index) == 0:
        #     warnings.warn(f"Tag {tag} not found")
        if get_status:
            df["status"] = df["status"].astype(int)
        df = df.tz_localize("UTC")
        return df.rename(columns={"value": tag, "status": tag + "::status"})

    def query_sql(
        self, query: str, parse: bool = True
    ) -> Union[pd.DataFrame, pyodbc.Cursor]:  # noqa:E501
        if not parse:
            cursor = self.conn.cursor()
            cursor.execute(query)
            return cursor
        else:
            res = pd.read_sql(query, self.conn)
            return res


class PIHandlerODBC:
    def __init__(self, host=None, port=None, options={}):
        self.host = host
        self.port = port
        self.conn = None
        self.cursor = None
        self._max_rows = options.get("max_rows", 100000)
        # TODO: Find default das_server under
        # HKLM\SOFTWARE\Wow6432Node\PISystem\Analytics\InstallData/AFServer
        # It seems that is actually not possible anymore.
        # ws3099.statoil.net
        self._das_server = options.get("das_server", "piwebapi.equinor.com")
        self._connection_string = options.get("connection_string", None)

        # print(self._das_server)

    def generate_connection_string(self):
        if self._connection_string is None:
            return (
                f"DRIVER={{PI ODBC Driver}};Server={self._das_server};"
                "Trusted_Connection=Yes;Command Timeout=1800;Provider Type=PIOLEDB;"
                f'Provider String={{Data source={self.host.replace(".statoil.net", "")};'  # noqa: E501
                "Integrated_Security=SSPI;Time Zone=UTC};"
            )
        else:
            return self._connection_string

    @staticmethod
    def generate_search_query(tag=None, desc=None):
        query = ["SELECT tag, descriptor as description FROM pipoint.pipoint2 WHERE"]
        if tag is not None:
            query.extend(["tag LIKE '{tag}'".format(tag=tag.replace("*", "%"))])
        if tag is not None and desc is not None:
            query.extend(["AND"])
        if desc is not None:
            query.extend(
                ["descriptor LIKE '{desc}'".format(desc=desc.replace("*", "%"))]
            )
        return " ".join(query)

    def generate_read_query(
        self,
        tag,
        start_time,
        stop_time,
        sample_time,
        read_type,
        metadata=None,
        get_status=False,
    ):
        if read_type in [
            ReaderType.COUNT,
            ReaderType.GOOD,
            ReaderType.BAD,
            ReaderType.TOTAL,
            ReaderType.SUM,
            ReaderType.SHAPEPRESERVING,
        ]:
            raise NotImplementedError

        if read_type == ReaderType.SNAPSHOT and stop_time is not None:
            raise NotImplementedError(
                "Timestamp not supported for PI ODBC connection using 'SNAPSHOT'."
                "Try the web API 'piwebapi' instead."
            )

        seconds = 0
        if read_type != ReaderType.SNAPSHOT:
            start_time = start_time.tz_convert("UTC")
            stop_time = stop_time.tz_convert("UTC")
            seconds = int(sample_time.total_seconds())
            if ReaderType.SAMPLED == read_type:
                seconds = 0
            else:
                if seconds <= 0:
                    pass  # Fixme: Not implemented
                    # sample_time = (stop_time-start_time).totalseconds

        timecast_format_query = "%d-%b-%y %H:%M:%S"  # 05-jan-18 14:00:00
        # timecast_format_output = "yyyy-MM-dd HH:mm:ss"

        source = {
            ReaderType.INT: "[piarchive]..[piinterp2]",
            ReaderType.MIN: "[piarchive]..[pimin]",
            ReaderType.MAX: "[piarchive]..[pimax]",
            ReaderType.RNG: "[piarchive]..[pirange]",
            ReaderType.AVG: "[piarchive]..[piavg]",
            ReaderType.VAR: "[piarchive]..[pistd]",
            ReaderType.STD: "[piarchive]..[pistd]",
            ReaderType.GOOD: "[piarchive]..[picount]",
            ReaderType.NOTGOOD: "[piarchive]..[picount]",
            ReaderType.TOTAL: "[piarchive]..[pitotal]",
            ReaderType.SUM: "[piarchive]..[pitotal]",
            ReaderType.COUNT: "[piarchive]..[picount]",
            ReaderType.SNAPSHOT: "[piarchive]..[pisnapshot]",
            ReaderType.SHAPEPRESERVING: "[piarchive]..[piplot]",
        }.get(read_type, "[piarchive]..[picomp2]")

        if ReaderType.VAR == read_type:
            query = ["SELECT POWER(CAST(value as FLOAT32), 2)"]
        elif ReaderType.GOOD == read_type:
            query = ["SELECT CAST(pctgood as FLOAT32)"]
        elif ReaderType.BAD == read_type:
            query = ["SELECT 100-CAST(pctgood as FLOAT32)"]
        elif ReaderType.RAW == read_type:
            query = [f"SELECT TOP {self._max_rows} CAST(value as FLOAT32)"]
        else:
            query = ["SELECT CAST(value as FLOAT32)"]

        # query.extend([f"AS value, FORMAT(time, '{timecast_format_output}') AS timestamp FROM {source} WHERE tag='{tag}'"])  # noqa E501
        query.extend(["AS value,"])

        if get_status:
            query.extend(["status, questionable, substituted,"])

        query.extend([f"time FROM {source} WHERE tag='{tag}'"])  # __utctime also works

        if ReaderType.SNAPSHOT != read_type:
            start = start_time.strftime(timecast_format_query)
            stop = stop_time.strftime(timecast_format_query)
            query.extend([f"AND (time BETWEEN '{start}' AND '{stop}')"])

        if ReaderType.GOOD == read_type:
            query.extend(["AND questionable = FALSE"])
        elif ReaderType.NOTGOOD == read_type:
            query.extend(["AND questionable = TRUE"])
        elif ReaderType.SHAPEPRESERVING == read_type:
            query.extend(
                [
                    f"AND (intervalcount = {int((stop_time-start_time).total_seconds()/seconds)})"  # noqa E501
                ]
            )
        elif ReaderType.RAW == read_type:
            pass
        elif read_type not in [ReaderType.SNAPSHOT, ReaderType.RAW]:
            query.extend([f"AND (timestep = '{seconds}s')"])

        if ReaderType.SNAPSHOT != read_type:
            query.extend(["ORDER BY time"])

        return " ".join(query)

    def set_options(self, options):
        pass

    def connect(self):
        connection_string = self.generate_connection_string()
        # The default autocommit=False is not supported by PI odbc driver.
        self.conn = pyodbc.connect(connection_string, autocommit=True)
        self.cursor = self.conn.cursor()

    def search(self, tag=None, desc=None):
        query = self.generate_search_query(tag, desc)
        self.cursor.execute(query)
        return self.cursor.fetchall()

    def _get_tag_metadata(self, tag):
        # Returns None if tag not found.
        query = f"SELECT digitalset, engunits, descriptor FROM pipoint.pipoint2 WHERE tag='{tag}'"  # noqa E501
        self.cursor.execute(query)
        desc = self.cursor.description
        col_names = [col[0] for col in desc]
        res = self.cursor.fetchone()
        if res is None:
            warnings.warn(f"Tag {tag} not found")
            return None
        metadata = dict(zip(col_names, res))
        return metadata

    def _get_tag_description(self, tag):
        query = f"SELECT descriptor FROM pipoint.pipoint2 WHERE tag='{tag}'"
        self.cursor.execute(query)
        desc = self.cursor.fetchone()
        return desc[0]

    def _get_tag_unit(self, tag):
        query = f"SELECT engunits FROM pipoint.pipoint2 WHERE tag='{tag}'"
        self.cursor.execute(query)
        unit = self.cursor.fetchone()
        return unit[0]

    @staticmethod
    def _is_summary(read_type):
        if read_type in [
            ReaderType.AVG,
            ReaderType.MIN,
            ReaderType.MAX,
            ReaderType.RNG,
            ReaderType.STD,
            ReaderType.VAR,
        ]:
            return True
        return False

    def read_tag(
        self,
        tag,
        start_time,
        stop_time,
        sample_time,
        read_type,
        metadata=None,
        get_status=False,
    ):
        if metadata is None:
            # Tag not found
            # TODO: Handle better and similarly across all handlers.
            return pd.DataFrame()

        query = self.generate_read_query(
            tag, start_time, stop_time, sample_time, read_type, get_status=get_status
        )
        # logging.debug(f'Executing SQL query {query!r}')
        df = pd.read_sql(
            query,
            self.conn,
            index_col="time",
            parse_dates={"time": "%Y-%m-%d %H:%M:%S"},
        ).fillna(value=np.nan)
        # PI ODBC reports aggregate values for both end points, using the end of
        # interval as anchor. Normalize to using start of insterval as anchor, and
        # remove initial point which is out of defined range.
        if self._is_summary(read_type):
            df.index = df.index - sample_time
            df = df.drop(df.index[0])

        df = df.tz_localize("UTC")

        if len(metadata["digitalset"]) > 0:
            self.cursor.execute(
                f"SELECT code, offset FROM pids WHERE digitalset='{metadata['digitalset']}'"  # noqa: E501
            )
            digitalset = self.cursor.fetchall()
            code = [x[0] for x in digitalset]
            offset = [x[1] for x in digitalset]
            df = df.replace(code, offset)

        if get_status:
            df["status"] = (
                # questionable and substituted are boolean, but no need to .astype(int)
                # status can be positive or negative for bad.
                df["questionable"]
                + 2 * (df["status"] != 0)
                + 4 * df["substituted"]
            )
            df = df.drop(columns=["questionable", "substituted"])

        return df.rename(columns={"value": tag, "status": tag + "::status"})

    def query_sql(
        self, query: str, parse: bool = True
    ) -> Union[pd.DataFrame, pyodbc.Cursor]:  # noqa: E501
        if not parse:
            cursor = self.conn.cursor()
            cursor.execute(query)
            return cursor
        else:
            res = pd.read_sql(query, self.conn)
            return res
