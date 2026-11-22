import QtQuick
import org.kde.kirigami as Kirigami

// Second window in dual mode: hosts the chat tabs. Closing it restores single-window mode.
Kirigami.ApplicationWindow {
    title: "aur-chat · chats"
    width: Kirigami.Units.gridUnit * 34
    height: Kirigami.Units.gridUnit * 32
    visible: backend.dualWindow
    onClosing: function(close) {
        close.accepted = false
        backend.setDualWindow(false)
    }

    pageStack.initialPage: Kirigami.Page {
        title: backend.activeFriendName || "Chats"
        actions: [
            Kirigami.Action {
                text: "Restore to one window"
                icon.name: "view-restore"
                onTriggered: backend.setDualWindow(false)
            }
        ]
        ChatPane {
            anchors.fill: parent
        }
    }
}
