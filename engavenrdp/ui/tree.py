"""The left-hand tree: virtual groups + files → groups → servers."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QAbstractItemView, QMessageBox, QTreeWidget, QTreeWidgetItem

from ..core.session import State
from ..model.nodes import ContainerNode, FileNode, Node, ServerNode, SmartGroupNode, TreeError
from . import icons, theme

ROLE_NODE = Qt.ItemDataRole.UserRole + 1
ROLE_VIRTUAL = Qt.ItemDataRole.UserRole + 2   # name of virtual group for headers / members
ROLE_MEMBER = Qt.ItemDataRole.UserRole + 3    # True for items that are references (not the real place)

VIRTUAL_GROUPS = (("connected", "Connected"), ("favorites", "Favorites"),
                  ("recent", "Recent"), ("reconnect", "Reconnect"))


class VirtualGroup:
    """Lightweight stand-in so the client area can show a thumbnail grid for it."""

    def __init__(self, key: str, label: str, servers: list[ServerNode]) -> None:
        self.key, self.name, self.label = key, label, label
        self.kind = "virtual"
        self._servers = servers

    def servers(self, recursive: bool = True) -> list[ServerNode]:
        return list(self._servers)


class ServerTree(QTreeWidget):
    node_selected = Signal(object)         # Node | VirtualGroup | None
    activated_server = Signal(object)      # ServerNode (double-click / Enter)
    structure_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setAnimated(True)
        self.setIndentation(16)
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setExpandsOnDoubleClick(False)
        self.files: list[FileNode] = []
        self.virtual_sources: dict[str, Callable[[], list[ServerNode]]] = {}
        self.show_virtual = True
        self.hidden_virtual: set[str] = set()
        self.state_of: Callable[[ServerNode], State] = lambda s: State.DISCONNECTED
        self.is_favorite: Callable[[ServerNode], bool] = lambda s: False
        self._items_by_server: dict[str, list[QTreeWidgetItem]] = {}
        self._filter = ""
        self._rebuilding = False
        self.itemSelectionChanged.connect(self._on_selection)
        self.itemDoubleClicked.connect(self._on_double)
        self.itemExpanded.connect(lambda it: self._set_expanded(it, True))
        self.itemCollapsed.connect(lambda it: self._set_expanded(it, False))

    # ------------------------------------------------------------ building
    def rebuild(self) -> None:
        self._rebuilding = True
        sel_ids = [n.id for n in self.selected_nodes() if isinstance(n, Node)]
        current = self.current_node()
        cur_id = current.id if isinstance(current, Node) else None
        cur_virtual = current.key if isinstance(current, VirtualGroup) else None
        vexp = {self.topLevelItem(i).data(0, ROLE_VIRTUAL): self.topLevelItem(i).isExpanded()
                for i in range(self.topLevelItemCount()) if self.topLevelItem(i).data(0, ROLE_VIRTUAL)}
        smart_exp = {}
        it = self._iter_items()
        for item in it:
            n = item.data(0, ROLE_NODE)
            if isinstance(n, SmartGroupNode):
                smart_exp[n.id] = item.isExpanded()
        self.clear()
        self._items_by_server.clear()
        dark = theme.is_dark()

        if self.show_virtual:
            for key, label in VIRTUAL_GROUPS:
                if key in self.hidden_virtual:
                    continue
                servers = self.virtual_sources.get(key, lambda: [])()
                vitem = QTreeWidgetItem([f"{label}  ({len(servers)})" if servers else label])
                vitem.setIcon(0, icons.virtual_icon(key, dark))
                vitem.setData(0, ROLE_VIRTUAL, key)
                vitem.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                f = vitem.font(0)
                f.setBold(True)
                vitem.setFont(0, f)
                self.addTopLevelItem(vitem)
                for s in servers:
                    self._add_server_item(vitem, s, member=True)
                vitem.setExpanded(vexp.get(key, key == "connected"))

        for f in self.files:
            fitem = self._make_item(f)
            self.addTopLevelItem(fitem)
            self._fill(fitem, f, smart_exp)
            fitem.setExpanded(f.expanded)

        self._rebuilding = False
        # restore selection
        restore = [i for i in self._iter_items()
                   if isinstance(i.data(0, ROLE_NODE), Node) and i.data(0, ROLE_NODE).id in sel_ids
                   and not i.data(0, ROLE_MEMBER)]
        target = None
        for i in self._iter_items():
            n = i.data(0, ROLE_NODE)
            if cur_id and isinstance(n, Node) and n.id == cur_id and not i.data(0, ROLE_MEMBER):
                target = i
            if cur_virtual and i.data(0, ROLE_VIRTUAL) == cur_virtual and n is None:
                target = i
        self.blockSignals(True)
        if target:
            self.setCurrentItem(target)
        for i in restore:
            i.setSelected(True)
        self.blockSignals(False)
        self.apply_filter(self._filter)

    def _make_item(self, node: Node, member: bool = False) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node.label])
        item.setData(0, ROLE_NODE, node)
        item.setData(0, ROLE_MEMBER, member)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not member and not isinstance(node, FileNode):
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        if isinstance(node, ContainerNode) and not isinstance(node, SmartGroupNode):
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        item.setFlags(flags)
        self._decorate(item)
        return item

    def _decorate(self, item: QTreeWidgetItem) -> None:
        node = item.data(0, ROLE_NODE)
        dark = theme.is_dark()
        tip = []
        if isinstance(node, FileNode):
            text = node.name + (" *" if node.dirty else "")
            item.setText(0, text)
            item.setIcon(0, icons.file_icon(dark, node.locked, node.dirty))
            tip.append(node.path_on_disk or "(not saved yet)")
            if node.locked:
                tip.append("Credentials locked: " + node.lock_reason)
        elif isinstance(node, SmartGroupNode):
            item.setText(0, node.name)
            item.setIcon(0, icons.folder_icon(dark, smart=True))
        elif isinstance(node, ContainerNode):
            item.setText(0, node.name)
            item.setIcon(0, icons.folder_icon(dark))
        elif isinstance(node, ServerNode):
            st = self.state_of(node)
            item.setText(0, node.label)
            item.setIcon(0, icons.server_icon(st, dark, self.is_favorite(node)))
            tip.append(node.host + (f"  ({node.display_name})" if node.display_name else ""))
            tip.append(f"Status: {st.value}")
            if item.data(0, ROLE_MEMBER) and node.parent is not None:
                tip.append(f"In: {node.file.name if node.file else ''}/{node.parent.path() if node.parent.parent else ''}")
        if isinstance(node, Node) and node.comment:
            tip.append(node.comment)
        item.setToolTip(0, "\n".join(t for t in tip if t))

    def _add_server_item(self, parent: QTreeWidgetItem, s: ServerNode, member: bool = False) -> None:
        it = self._make_item(s, member)
        parent.addChild(it)
        self._items_by_server.setdefault(s.id, []).append(it)

    def _fill(self, parent_item: QTreeWidgetItem, node: ContainerNode, smart_exp: dict) -> None:
        for c in node.children:
            if isinstance(c, ServerNode):
                self._add_server_item(parent_item, c)
            elif isinstance(c, SmartGroupNode):
                it = self._make_item(c)
                parent_item.addChild(it)
                for m in c.members():
                    self._add_server_item(it, m, member=True)
                it.setExpanded(smart_exp.get(c.id, False))
            elif isinstance(c, ContainerNode):
                it = self._make_item(c)
                parent_item.addChild(it)
                self._fill(it, c, smart_exp)
                it.setExpanded(c.expanded)

    def _iter_items(self):
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            it = stack.pop(0)
            yield it
            stack[0:0] = [it.child(i) for i in range(it.childCount())]

    def _set_expanded(self, item: QTreeWidgetItem, on: bool) -> None:
        if self._rebuilding:
            return
        n = item.data(0, ROLE_NODE)
        if isinstance(n, ContainerNode) and not isinstance(n, SmartGroupNode) and not item.data(0, ROLE_MEMBER):
            n.expanded = on

    # ------------------------------------------------------------- updates
    def update_server(self, server: ServerNode) -> None:
        for it in self._items_by_server.get(server.id, []):
            self._decorate(it)

    def refresh_decorations(self) -> None:
        for it in self._iter_items():
            if it.data(0, ROLE_NODE) is not None:
                self._decorate(it)

    # ------------------------------------------------------------ selection
    def item_object(self, item: QTreeWidgetItem | None):
        if item is None:
            return None
        n = item.data(0, ROLE_NODE)
        if n is not None:
            return n
        key = item.data(0, ROLE_VIRTUAL)
        if key:
            label = dict(VIRTUAL_GROUPS)[key]
            return VirtualGroup(key, label, self.virtual_sources.get(key, lambda: [])())
        return None

    def current_node(self):
        return self.item_object(self.currentItem())

    def selected_nodes(self) -> list:
        out = []
        for it in self.selectedItems():
            n = self.item_object(it)
            if n is not None and n not in out:
                out.append(n)
        return out

    def selected_servers(self) -> list[ServerNode]:
        """Servers implied by the selection (groups expand to their servers)."""
        out: list[ServerNode] = []
        seen = set()
        for n in self.selected_nodes():
            if isinstance(n, ServerNode):
                cands = [n]
            elif isinstance(n, SmartGroupNode):
                cands = n.members()
            elif isinstance(n, (ContainerNode, VirtualGroup)):
                cands = n.servers()
            else:
                cands = []
            for s in cands:
                if s.id not in seen:
                    seen.add(s.id)
                    out.append(s)
        return out

    def select_node(self, node) -> None:
        for it in self._iter_items():
            n = it.data(0, ROLE_NODE)
            if n is node and not it.data(0, ROLE_MEMBER):
                self.clearSelection()
                self.setCurrentItem(it)
                self.scrollToItem(it)
                return
        for it in self._iter_items():  # fall back to a virtual member
            if it.data(0, ROLE_NODE) is node:
                self.setCurrentItem(it)
                return

    def _on_selection(self) -> None:
        if not self._rebuilding:
            self.node_selected.emit(self.current_node())

    def _on_double(self, item: QTreeWidgetItem, _col: int) -> None:
        n = item.data(0, ROLE_NODE)
        if isinstance(n, ServerNode):
            self.activated_server.emit(n)
        else:
            item.setExpanded(not item.isExpanded())

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() == Qt.KeyboardModifier.NoModifier:
            n = self.current_node()
            if isinstance(n, ServerNode):
                self.activated_server.emit(n)
                return
        super().keyPressEvent(e)

    # --------------------------------------------------------------- filter
    def apply_filter(self, text: str) -> None:
        self._filter = text.strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            n = item.data(0, ROLE_NODE)
            child_match = False
            for i in range(item.childCount()):
                if visit(item.child(i)):
                    child_match = True
            if not self._filter:
                item.setHidden(False)
                return True
            me = False
            if isinstance(n, ServerNode):
                me = n.matches(self._filter)
            elif isinstance(n, Node):
                me = self._filter in n.name.lower()
            show = me or child_match
            item.setHidden(not show)
            if child_match and self._filter:
                item.setExpanded(True)
            return show

        for i in range(self.topLevelItemCount()):
            visit(self.topLevelItem(i))

    # ---------------------------------------------------------- drag & drop
    def _drop_target(self, event) -> tuple[ContainerNode | None, int | None]:
        pos = event.position().toPoint()
        item = self.itemAt(pos)
        if item is None or item.data(0, ROLE_MEMBER):
            return None, None
        node = item.data(0, ROLE_NODE)
        indicator = self.dropIndicatorPosition()
        P = QAbstractItemView.DropIndicatorPosition
        if indicator == P.OnItem:
            if isinstance(node, ContainerNode):
                return node, None
            return (node.parent, node.parent.children.index(node) + 1) if node.parent else (None, None)
        if indicator in (P.AboveItem, P.BelowItem):
            if isinstance(node, FileNode):
                return None, None
            if node.parent is None:
                return None, None
            idx = node.parent.children.index(node) + (1 if indicator == P.BelowItem else 0)
            return node.parent, idx
        return None, None

    def _dragged(self) -> list[Node]:
        nodes = [n for n in self.selected_nodes()
                 if isinstance(n, Node) and not isinstance(n, FileNode)]
        # skip nodes whose ancestor is also dragged
        ids = {n.id for n in nodes}
        return [n for n in nodes if not any(a.id in ids for a in n.ancestors())]

    def dragMoveEvent(self, event) -> None:
        super().dragMoveEvent(event)
        target, _ = self._drop_target(event)
        nodes = self._dragged()
        if target is None or not nodes or not all(target.can_accept(n) or n.parent is target for n in nodes):
            event.ignore()
        else:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        target, index = self._drop_target(event)
        nodes = self._dragged()
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if target is None or not nodes:
            return
        try:
            for n in nodes:
                old_parent = n.parent
                if old_parent is target and index is not None:
                    cur = target.children.index(n)
                    if cur < index:
                        index -= 1
                if old_parent is not None:
                    old_parent.mark_dirty()
                target.add(n, index)
                if index is not None:
                    index += 1
        except TreeError as e:
            QMessageBox.warning(self, "Cannot move", str(e))
        self.structure_changed.emit()

    def startDrag(self, actions) -> None:
        if self._dragged():
            super().startDrag(Qt.DropAction.MoveAction)

    # needed so Qt does not try to move QTreeWidgetItems itself
    def dropMimeData(self, parent, index, data, action) -> bool:  # pragma: no cover
        return False

    def indexFromNode(self, node) -> QModelIndex:  # pragma: no cover - helper
        for it in self._iter_items():
            if it.data(0, ROLE_NODE) is node:
                return self.indexFromItem(it)
        return QModelIndex()
