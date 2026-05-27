from pathlib import Path

from gsim.data import DataManagerMapped
from gsim.utils.NioData import NIO_MATRIX


class DmgrAutoCCAll(DataManagerMapped):
    def __init__(self):
        DataManagerMapped.__init__(self)
        self.dataPath = None
        self._fields = []
        self._data = {}

    def initialize(self, id, path, cfg):
        DataManagerMapped.initialize(self, id, path, cfg)
        self.dataPath = Path(cfg.getAttributeString("dataPath"))
        prefix = f"{self.tag}."
        for data_file in sorted(self.dataPath.glob("*.npy")):
            name = data_file.name[:-4]
            field = name[len(prefix):] if name.startswith(prefix) else name
            if not field or field.startswith("."):
                continue
            obj = NIO_MATRIX()
            self._fields.append(field)
            self._data[field] = obj
            setattr(self, self._safe_attr(field), obj)
            self.addDailyData(obj, name)

    def loadDay(self, di):
        return

    @staticmethod
    def _safe_attr(field):
        value = []
        for char in field:
            value.append(char if char.isalnum() or char == "_" else "_")
        name = "".join(value)
        if not name or name[0].isdigit():
            name = f"field_{name}"
        return name
