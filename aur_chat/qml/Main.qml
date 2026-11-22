import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kirigami.dialogs as KDialog
Kirigami.ApplicationWindow {
    id: root
    title: "aur-chat · " + backend.presenceText
    width: 920
    height: 600

    property bool addFriendOpen: false
    property bool renameOpen: false
    property bool settingsOpen: false
    readonly property int fontDefault: Kirigami.Theme.defaultFont.pixelSize
    function syncFontSpin() {
        fontSpin.value = backend.friendFontSize > 0 ? backend.friendFontSize : root.fontDefault
        chatSpin.value = backend.chatFontSize > 0 ? backend.chatFontSize : root.fontDefault
    }
    Connections {
        target: backend
        function onFriendFontChanged() { if (root.settingsOpen) root.syncFontSpin() }
        function onChatFontChanged() { if (root.settingsOpen) root.syncFontSpin() }
    }

    onVisibilityChanged: function(vis) { backend.setWindowVisible(vis !== Qt.WindowHidden && vis !== Qt.WindowMinimized) }

    onClosing: function(close) {
        if (trayOk) {
            close.accepted = false
            root.showMinimized()
        } else {
            backend.shutdown()
        }
    }

    Connections {
        target: backend
        function onPresenceModeChanged() {
            const i = backend.presenceModes.indexOf(backend.presenceMode)
            if (i >= 0 && presenceBox.currentIndex !== i)
                presenceBox.currentIndex = i
        }
    }

    pageStack.initialPage: Kirigami.Page {
        title: backend.playingTitle
        actions: [
            Kirigami.Action {
                text: "Refresh"
                icon.name: "view-refresh"
                shortcut: "F5"
                onTriggered: {
                    if (mainTabs.currentIndex === 0)
                        backend.refreshFriends()
                    else
                        backend.refreshGames(gameSearch.text)
                }
            },
            Kirigami.Action {
                text: "Add friend"
                icon.name: "list-add-user"
                visible: mainTabs.currentIndex === 0
                onTriggered: root.addFriendOpen = true
            },
            Kirigami.Action {
                text: backend.dualWindow ? "Restore to one window" : "Dual window"
                icon.name: "window-duplicate"
                visible: mainTabs.currentIndex === 0
                onTriggered: backend.setDualWindow(!backend.dualWindow)
            }
        ]

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            RowLayout {
                id: mainTabs
                Layout.fillWidth: true
                property int currentIndex: chatTab.checked ? 0 : 1
                function goGames() {
                    gamesTab.checked = true
                    if (backend.gameItems.length === 0)
                        backend.refreshGames(gameSearch.text)
                }
                QQC2.ToolButton {
                    id: chatTab
                    text: "Chat"
                    checkable: true
                    checked: true
                    onClicked: { checked = true; gamesTab.checked = false }
                }
                QQC2.ToolButton {
                    id: gamesTab
                    text: "Games"
                    checkable: true
                    onClicked: { checked = true; chatTab.checked = false; mainTabs.goGames() }
                }
                Item { Layout.fillWidth: true }
                QQC2.ToolButton {
                    id: ownNameBtn
                    text: backend.ownName || "…"
                    font.bold: true
                    onClicked: { renameField.text = backend.ownName; root.renameOpen = true }
                    QQC2.ToolTip.visible: hovered
                    QQC2.ToolTip.text: "Change your profile name"
                }
                Text {
                    text: backend.presenceText
                    color: Kirigami.Theme.textColor
                    opacity: 0.8
                    elide: Text.ElideRight
                    Layout.maximumWidth: root.width * 0.4
                }
                QQC2.Button {
                    visible: backend.gameRunning
                    enabled: !backend.busy
                    text: "End game"
                    onClicked: backend.endGame(false)
                }
                QQC2.Button {
                    visible: backend.gameRunning
                    enabled: !backend.busy
                    text: "Force end"
                    onClicked: backend.endGame(true)
                }
                QQC2.ComboBox {
                    id: presenceBox
                    model: backend.presenceOptions
                    currentIndex: backend.presenceModes.indexOf(backend.presenceMode)
                    enabled: !backend.busy
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 9
                    onActivated: backend.setPresence(backend.presenceModes[index])
                    QQC2.ToolTip.visible: hovered
                    QQC2.ToolTip.text: "Set Steam status"
                }
                QQC2.ToolButton {
                    objectName: "settingsBtn"
                    icon.name: "settings-configure"
                    onClicked: settingsDialog.open()
                    QQC2.ToolTip.visible: hovered
                    QQC2.ToolTip.text: "Settings"
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: mainTabs.currentIndex

                QQC2.SplitView {
                    id: chatSplit
                    orientation: Qt.Horizontal
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    handle: Rectangle {
                        implicitWidth: 4
                        property bool dragging: QQC2.SplitHandle.pressed
                        onDraggingChanged: if (!dragging) splitSave.restart()  // save once per drag
                        color: dragging ? Kirigami.Theme.highlightColor
                            : QQC2.SplitHandle.hovered ? Kirigami.Theme.hoverColor
                            : Kirigami.Theme.disabledTextColor
                        HoverHandler { cursorShape: Qt.SplitHCursor }
                    }
                    Timer {
                        id: splitSave
                        interval: 400
                        onTriggered: backend.setFriendsWidth(friendsCol.width)
                    }

            ColumnLayout {
                id: friendsCol
                QQC2.SplitView.preferredWidth: backend.friendsWidth > 0
                    ? backend.friendsWidth : Math.min(300, root.width * 0.34)
                QQC2.SplitView.minimumWidth: 220
                onWidthChanged: if (chatSplit.dragging) splitSave.restart()
                Layout.fillHeight: true
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Heading {
                    level: 3
                    Layout.fillWidth: true
                    Layout.leftMargin: Kirigami.Units.gridUnit
                    text: "Friends"
                }
                Text {
                    Layout.fillWidth: true
                    Layout.leftMargin: Kirigami.Units.gridUnit
                    text: backend.statusText
                    opacity: 0.7
                    elide: Text.ElideRight
                    color: Kirigami.Theme.textColor
                }
                QQC2.TextField {
                    id: friendSearch
                    Layout.fillWidth: true
                    Layout.leftMargin: Kirigami.Units.gridUnit
                    Layout.rightMargin: Kirigami.Units.gridUnit
                    placeholderText: "Filter friends…"
                    onTextChanged: backend.setFriendFilter(text)
                }
                QQC2.CheckBox {
                    Layout.leftMargin: Kirigami.Units.gridUnit
                    text: "Hide offline"
                    checked: backend.hideOffline
                    onToggled: backend.setHideOffline(checked)
                }

                ListView {
                    id: friendList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: backend.friendItems
                    currentIndex: -1
                    delegate: QQC2.ItemDelegate {
                        id: row
                        readonly property int baseSize: backend.friendFontSize > 0
                            ? backend.friendFontSize : Kirigami.Theme.defaultFont.pixelSize
                        width: friendList.width
                        highlighted: modelData.steamId === backend.activeFriendId
                        onPressed: backend.pickFriend(modelData.steamId)  // press, not click: list may reorder mid-click
                        QQC2.ToolTip.visible: hovered && (modelData.lastSeen.length > 0 || modelData.richText.length > 0)
                        QQC2.ToolTip.text: [modelData.lastSeen, modelData.richText].filter(Boolean).join("\n")
                        background: Rectangle {
                            color: modelData.unread > 0
                                ? Qt.alpha(Kirigami.Theme.highlightColor, 0.25) : "transparent"
                        }
                        contentItem: ColumnLayout {
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Kirigami.Units.smallSpacing
                                Rectangle {
                                    Layout.alignment: Qt.AlignVCenter
                                    Layout.rightMargin: Kirigami.Units.smallSpacing
                                    width: Kirigami.Units.gridUnit * 0.5
                                    height: width
                                    radius: width / 2
                                    color: modelData.statusColor
                                }
                                Text {
                                    text: modelData.name
                                    font.bold: row.highlighted || modelData.unread > 0
                                    font.pixelSize: row.baseSize
                                    color: Kirigami.Theme.textColor
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: "★"
                                    visible: modelData.favourite
                                    color: "#f5c211"
                                    font.pixelSize: row.baseSize
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                Rectangle {
                                    visible: modelData.unread > 0
                                    Layout.alignment: Qt.AlignVCenter
                                    implicitWidth: Math.max(unreadText.implicitWidth + 8, implicitHeight)
                                    implicitHeight: unreadText.implicitHeight + 2
                                    radius: height / 2
                                    color: Kirigami.Theme.highlightColor
                                    Text {
                                        id: unreadText
                                        anchors.centerIn: parent
                                        text: modelData.unread
                                        color: Kirigami.Theme.highlightedTextColor
                                        font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                    }
                                }
                                Rectangle {
                                    id: deviceBadge
                                    visible: (modelData.device || "").length > 0
                                    Layout.alignment: Qt.AlignVCenter
                                    implicitWidth: deviceRow.implicitWidth +
                                        Kirigami.Units.smallSpacing * 2
                                    implicitHeight: Math.max(deviceIcon.height,
                                        deviceText.implicitHeight) + 4
                                    radius: height / 2
                                    color: Kirigami.Theme.alternateBackgroundColor
                                    RowLayout {
                                        id: deviceRow
                                        anchors.centerIn: parent
                                        spacing: 3
                                        Kirigami.Icon {
                                            id: deviceIcon
                                            source: modelData.deviceIcon
                                            implicitWidth: 14
                                            implicitHeight: 14
                                            visible: source.length > 0
                                        }
                                        Text {
                                            id: deviceText
                                            text: modelData.device
                                            color: Kirigami.Theme.disabledTextColor
                                            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                        }
                                    }
                                }
                            }
                            Text {
                                // status · game · game context (Dota hero + level, joinable hint)
                                text: {
                                    const parts = [modelData.status]
                                    if (modelData.game) parts.push(modelData.game)
                                    if (modelData.gameDetail) parts.push(modelData.gameDetail)
                                    return parts.join(" · ")
                                }
                                font.pixelSize: Math.max(9, Math.round(row.baseSize * 0.85))
                                color: Kirigami.Theme.disabledTextColor
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        TapHandler {
                            acceptedButtons: Qt.RightButton
                            onTapped: {
                                friendMenu.steamId = modelData.steamId
                                friendMenu.favourite = modelData.favourite
                                friendMenu.popup(row, point.position.x, point.position.y)
                            }
                        }
                    }
                }
            }

            QQC2.Menu {
                id: friendMenu
                property string steamId: ""
                property bool favourite: false
                QQC2.MenuItem {
                    text: "Open chat"
                    onTriggered: backend.pickFriend(friendMenu.steamId)
                }
                QQC2.MenuItem {
                    text: friendMenu.favourite ? "Remove from favourites" : "Add to favourites"
                    onTriggered: backend.toggleFavourite(friendMenu.steamId)
                }
            }

            ChatPane {
                QQC2.SplitView.fillWidth: true
                QQC2.SplitView.minimumWidth: 260
                visible: !backend.dualWindow
            }

            Kirigami.PlaceholderMessage {
                QQC2.SplitView.fillWidth: true
                visible: backend.dualWindow
                icon.name: "window-duplicate"
                text: "Chats are open in the chat window"
            }
                }

                ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing
                    Layout.margins: Kirigami.Units.gridUnit

                    RowLayout {
                        Layout.fillWidth: true
                        QQC2.CheckBox {
                            text: "Installed only"
                            checked: true
                            onCheckedChanged: backend.setGamesInstalledOnly(checked)
                        }
                        QQC2.CheckBox {
                            text: "Steam shortcuts"
                            checked: true
                            onCheckedChanged: backend.setGamesIncludeShortcuts(checked)
                        }
                        QQC2.TextField {
                            id: gameSearch
                            Layout.fillWidth: true
                            placeholderText: "Search library…"
                            onAccepted: backend.refreshGames(text)
                        }
                        QQC2.Button {
                            text: "Play"
                            enabled: gameList.currentIndex >= 0 && !backend.busy
                            onClicked: {
                                if (gameList.currentIndex >= 0)
                                    backend.playGame(backend.gameItems[gameList.currentIndex].appId)
                            }
                        }
                        QQC2.Button {
                            text: "Update"
                            enabled: gameList.currentIndex >= 0 && !!backend.gameItems[gameList.currentIndex]
                                && backend.gameItems[gameList.currentIndex].updatable
                            onClicked: backend.updateGame(backend.gameItems[gameList.currentIndex].appId)
                        }
                        QQC2.Button {
                            text: "Check updates"
                            onClicked: backend.refreshUpdates()
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: backend.gamesStatus
                        opacity: 0.75
                        color: Kirigami.Theme.textColor
                        elide: Text.ElideRight
                    }

                    QQC2.Menu {
                        id: gameCtx
                        property string appId: ""
                        QQC2.MenuItem {
                            text: "Configure launch options…"
                            onTriggered: backend.openLaunchOptions(gameCtx.appId)
                        }
                        QQC2.MenuItem {
                            text: "Play"
                            onTriggered: backend.playGame(gameCtx.appId)
                        }
                    }

                    ListView {
                        id: gameList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: backend.gameItems
                        currentIndex: -1
                        delegate: Item {
                            width: gameList.width
                            height: del.implicitHeight
                            QQC2.ItemDelegate {
                                id: del
                                width: parent.width
                                highlighted: gameList.currentIndex === index
                                onClicked: gameList.currentIndex = index
                                onDoubleClicked: backend.playGame(modelData.appId)
                                contentItem: ColumnLayout {
                                    spacing: 2
                                    Text {
                                        text: modelData.name
                                        font.bold: gameList.currentIndex === index
                                        color: Kirigami.Theme.textColor
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                    }
                                    Text {
                                        visible: !!modelData.updateState
                                        text: modelData.updateState || ""
                                        font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                        color: modelData.updatable ? Kirigami.Theme.neutralTextColor
                                            : Kirigami.Theme.positiveTextColor
                                    }
                                    QQC2.ProgressBar {
                                        visible: !!modelData.updating
                                        Layout.fillWidth: true
                                        indeterminate: true
                                    }
                                    Text {
                                        visible: !!modelData.updateText
                                        text: modelData.updateText || ""
                                        font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                    }
                                    Text {
                                        text: {
                                            const o = modelData.launchOptions || ""
                                            if (modelData.isShortcut) {
                                                const tool = modelData.compatTool || "native"
                                                return o ? ("Shortcut · " + tool + " · " + o)
                                                         : ("Shortcut · " + tool + " · " + modelData.appId)
                                            }
                                            if (o) return "Launch options · " + o
                                            return modelData.installed ? ("Play · " + modelData.appId) : ("Not installed · " + modelData.appId)
                                        }
                                        font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                        color: Kirigami.Theme.disabledTextColor
                                        elide: Text.ElideRight
                                        maximumLineCount: 2
                                        wrapMode: Text.Wrap
                                        Layout.fillWidth: true
                                    }
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.RightButton
                                onClicked: function(mouse) {
                                    gameList.currentIndex = index
                                    gameCtx.appId = modelData.appId
                                    gameCtx.popup(del)
                                }
                            }
                        }
                    }
                }
            }
        }
    }


    KDialog.Dialog {
        visible: backend.launchOptionsOpen
        title: "Launch options — " + backend.launchOptionsName
        onVisibleChanged: if (visible) launchOptsArea.text = backend.launchOptionsText
        standardButtons: KDialog.Dialog.NoButton
        preferredWidth: Kirigami.Units.gridUnit * 36
        ColumnLayout {
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "Steam launch options. Use %command% where Steam expects it. Shortcuts launch directly with their saved executable and compatibility tool."
                opacity: 0.8
                color: Kirigami.Theme.textColor
            }
            QQC2.TextArea {
                id: launchOptsArea
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.gridUnit * 10
                wrapMode: Text.Wrap
                placeholderText: "e.g. PROTON_USE_WOW64=0 gamemoderun %command%"
            }
            RowLayout {
                Layout.fillWidth: true
                QQC2.Button { text: "Cancel"; onClicked: backend.closeLaunchOptions() }
                Item { Layout.fillWidth: true }
                QQC2.Button {
                    text: "Save"
                    onClicked: backend.saveLaunchOptions(launchOptsArea.text)
                }
            }
        }
    }

    KDialog.Dialog {
        visible: root.addFriendOpen
        title: "Add friend"
        onVisibleChanged: if (!visible) searchField.text = ""
        standardButtons: KDialog.Dialog.NoButton
        preferredWidth: Kirigami.Units.gridUnit * 28
        ColumnLayout {
            Kirigami.FormLayout {
                Layout.fillWidth: true
                QQC2.TextField {
                    id: searchField
                    Kirigami.FormData.label: "Steam ID / URL / vanity"
                    placeholderText: "gabelogannewell or profile URL"
                    onAccepted: backend.searchFriend(text)
                }
            }
            Kirigami.Heading {
                level: 3
                Layout.fillWidth: true
                text: {
                    const r = backend.searchResult
                    if (!r) return ""
                    if (r.error) return String(r.error)
                    return (r.persona_name || "?") + " · " + r.steam_id
                }
                visible: backend.searchResult != null
            }
            RowLayout {
                Layout.fillWidth: true
                QQC2.Button { text: "Close"; onClicked: root.addFriendOpen = false }
                Item { Layout.fillWidth: true }
                QQC2.Button {
                    text: "Search"
                    enabled: searchField.text.length > 0 && !backend.busy
                    onClicked: backend.searchFriend(searchField.text)
                }
                QQC2.Button {
                    text: "Send request"
                    enabled: backend.searchResult != null && backend.searchResult.steam_id && !backend.busy
                    onClicked: { backend.addFriend(searchField.text); root.addFriendOpen = false }
                }
            }
        }
    }

    KDialog.Dialog {
        visible: root.renameOpen
        title: "Profile name"
        standardButtons: KDialog.Dialog.NoButton
        preferredWidth: Kirigami.Units.gridUnit * 24
        ColumnLayout {
            QQC2.TextField {
                id: renameField
                Layout.fillWidth: true
                placeholderText: "New profile name"
                onAccepted: renameSave.clicked()
            }
            RowLayout {
                Layout.fillWidth: true
                QQC2.Button { text: "Cancel"; onClicked: root.renameOpen = false }
                Item { Layout.fillWidth: true }
                QQC2.Button {
                    id: renameSave
                    text: "Save"
                    enabled: renameField.text.trim().length > 0 && !backend.busy
                    onClicked: { backend.setOwnName(renameField.text); root.renameOpen = false }
                }
            }
        }
    }

    KDialog.Dialog {
        id: settingsDialog
        // imperative open/close: dialog-driven dismissal (Esc, click-outside) would
        // kill a `visible: root.settingsOpen` binding and the dialog could never reopen
        title: "Settings"
        onVisibleChanged: {
            root.settingsOpen = visible  // mirror for the font-change handlers
            if (visible) root.syncFontSpin()
        }
        standardButtons: KDialog.Dialog.NoButton
        preferredWidth: Kirigami.Units.gridUnit * 22
        ColumnLayout {
            Kirigami.FormLayout {
                QQC2.SpinBox {
                    id: fontSpin
                    Kirigami.FormData.label: "Friends list font (px)"
                    from: 8
                    to: 24
                    editable: false
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 7
                    onValueModified: backend.setFriendFontSize(value)
                }
                QQC2.SpinBox {
                    id: chatSpin
                    Kirigami.FormData.label: "Chat font (px)"
                    from: 8
                    to: 24
                    editable: false
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 7
                    onValueModified: backend.setChatFontSize(value)
                }
                QQC2.SpinBox {
                    id: pollSpin
                    Kirigami.FormData.label: "Friends refresh (s)"
                    from: 2
                    to: 120
                    editable: false
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 7
                    value: backend.friendsInterval
                    onValueModified: backend.setFriendsInterval(value)
                }
                QQC2.SpinBox {
                    id: gamePollSpin
                    Kirigami.FormData.label: "Game status poll (s)"
                    from: 1
                    to: 60
                    editable: false
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 7
                    value: backend.gamesInterval
                    onValueModified: backend.setGamesInterval(value)
                }
                QQC2.ComboBox {
                    id: animCombo
                    Kirigami.FormData.label: "Animation speed"
                    editable: false
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 7
                    model: ["Normal", "Half", "Quarter", "Off"]
                    currentIndex: backend.animIndex
                    onActivated: backend.setAnimSpeed(currentIndex)
                }
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.7
                font.pointSize: Kirigami.Theme.defaultFont.pointSize * 0.9
                text: "Background refreshes are silent — the friends list updates in place every %1s, the running-game check every %2s. Chat messages arrive instantly via the daemon stream; no polling involved."
                    .arg(backend.friendsInterval).arg(backend.gamesInterval)
            }
            RowLayout {
                Layout.fillWidth: true
                QQC2.Button {
                    text: "Reset to default"
                    onClicked: { backend.setFriendFontSize(0); backend.setChatFontSize(0) }
                }
                Item { Layout.fillWidth: true }
                QQC2.Button { text: "Close"; onClicked: settingsDialog.close() }
            }
        }
    }

    ChatWindow {}
}
