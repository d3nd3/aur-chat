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

    onVisibilityChanged: function(vis) { backend.setWindowVisible(vis !== Qt.WindowHidden) }

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
        function onAuthRequiredChanged() { authBanner.visible = backend.authRequired }
        function onPresenceModeChanged() {
            const i = backend.presenceModes.indexOf(backend.presenceMode)
            if (i >= 0 && presenceBox.currentIndex !== i)
                presenceBox.currentIndex = i
        }
    }
    Component.onCompleted: authBanner.visible = backend.authRequired

    pageStack.initialPage: Kirigami.Page {
        title: backend.activeFriendName || "aur-chat"
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
                    text: backend.stopForce ? "Force end" : "End game"
                    onClicked: backend.endGame()
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
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: mainTabs.currentIndex

                RowLayout {
                    spacing: 0

            ColumnLayout {
                Layout.preferredWidth: Math.min(300, root.width * 0.34)
                Layout.minimumWidth: 220
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

                ListView {
                    id: friendList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: backend.friendItems
                    currentIndex: -1
                    delegate: QQC2.ItemDelegate {
                        id: row
                        width: friendList.width
                        highlighted: modelData.steamId === backend.activeFriendId
                        onPressed: backend.pickFriend(modelData.steamId)  // press, not click: list may reorder mid-click
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
                                    width: Kirigami.Units.smallSpacing
                                    height: width
                                    radius: width / 2
                                    color: modelData.statusColor
                                }
                                Text {
                                    text: modelData.name
                                    font.bold: row.highlighted || modelData.unread > 0
                                    color: Kirigami.Theme.textColor
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
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
                                    implicitWidth: deviceText.implicitWidth +
                                        Kirigami.Units.smallSpacing * 2
                                    implicitHeight: deviceText.implicitHeight + 2
                                    radius: height / 2
                                    color: Kirigami.Theme.alternateBackgroundColor
                                    Text {
                                        id: deviceText
                                        anchors.centerIn: parent
                                        text: (modelData.deviceIcon || "") + " " +
                                            (modelData.device || "")
                                        color: Kirigami.Theme.disabledTextColor
                                        font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                    }
                                }
                            }
                            Text {
                                text: modelData.game ? modelData.status + " · " + modelData.game : modelData.status
                                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                color: Kirigami.Theme.disabledTextColor
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.preferredWidth: 1
                Layout.fillHeight: true
                color: Kirigami.Theme.disabledTextColor
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Kirigami.InlineMessage {
                    id: authBanner
                    Layout.fillWidth: true
                    type: Kirigami.MessageType.Error
                    text: "Not logged in — run `aurelia login` (or `aurelia login --qr`), then F5."
                    visible: false
                }

                Kirigami.Heading {
                    level: 2
                    Layout.fillWidth: true
                    Layout.leftMargin: Kirigami.Units.gridUnit
                    Layout.topMargin: Kirigami.Units.smallSpacing
                    text: backend.activeFriendName || "Select a friend"
                    opacity: backend.activeFriendId ? 1 : 0.6
                }

                ListView {
                    id: chatView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: Kirigami.Units.smallSpacing
                    boundsBehavior: Flickable.StopAtBounds
                    model: backend.messageItems
                    property bool pinned: true  // follow new messages only while at the bottom
                    onMovingChanged: if (!moving) pinned = atYEnd
                    function scrollToLatest() {
                        Qt.callLater(function() {
                            if (chatView.count > 0 && chatView.pinned)
                                chatView.positionViewAtEnd()
                        })
                    }
                    onCountChanged: {
                        if (count === 0) pinned = true
                        scrollToLatest()
                    }
                    onContentHeightChanged: if (count > 0) scrollToLatest()
                    delegate: Item {
                        id: row
                        width: chatView.width
                        height: bubble.y + bubble.height + Kirigami.Units.smallSpacing
                        readonly property bool fromSelf: modelData.fromSelf
                        readonly property string msg: modelData.text
                        readonly property string time: modelData.timestamp || ""
                        readonly property string imageUrl: modelData.imageUrl || ""
                        readonly property real pad: Kirigami.Units.smallSpacing
                        readonly property real maxW: chatView.width * 0.78
                        readonly property real maxImageW: Math.max(1, Math.min(
                            row.maxW, chatView.width - Kirigami.Units.gridUnit * 2
                        ))
                        readonly property bool hasImage: row.imageUrl !== "" &&
                            media.status === Image.Ready && media.implicitWidth > 0
                        readonly property real imageW: row.hasImage
                            ? Math.min(media.implicitWidth, row.maxImageW) : 0
                        readonly property real imageH: row.hasImage
                            ? media.implicitHeight * row.imageW / media.implicitWidth : 0

                        Rectangle {
                            id: bubble
                            y: pad
                            anchors.right: row.fromSelf ? parent.right : undefined
                            anchors.left: row.fromSelf ? undefined : parent.left
                            anchors.rightMargin: Kirigami.Units.gridUnit
                            anchors.leftMargin: Kirigami.Units.gridUnit
                            width: row.hasImage
                                ? row.imageW + row.pad * 2
                                : Math.min(row.maxW, chatView.width - Kirigami.Units.gridUnit * 2)
                            height: (media.visible ? row.imageH : body.paintedHeight) +
                                (stamp.visible ? stamp.paintedHeight + row.pad * 0.25 : 0) +
                                row.pad * 2
                            radius: Kirigami.Units.gridUnit * 0.5
                            color: row.fromSelf ? Kirigami.Theme.highlightColor : Kirigami.Theme.alternateBackgroundColor
                            Image {
                                id: media
                                x: row.pad
                                y: row.pad
                                width: row.imageW
                                height: row.imageH
                                source: row.imageUrl
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true
                                cache: true
                                visible: row.hasImage
                                MouseArea {
                                    anchors.fill: parent
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    onClicked: function(mouse) {
                                        if (mouse.button === Qt.RightButton) {
                                            imageMenu.imageUrl = row.imageUrl
                                            imageMenu.popup(media, mouse.x, mouse.y)
                                        } else {
                                            imagePopup.imageUrl = row.imageUrl
                                            imagePopup.open()
                                        }
                                    }
                                }
                            }
                            TextEdit {
                                id: body
                                x: row.pad
                                y: row.pad
                                width: parent.width - row.pad * 2
                                text: row.msg
                                visible: !media.visible
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.Wrap
                                color: row.fromSelf ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                            }
                            Text {
                                id: stamp
                                x: row.pad
                                y: (media.visible ? media.y + media.height : body.y + body.paintedHeight) +
                                    row.pad * 0.25
                                width: parent.width - row.pad * 2
                                text: row.time
                                visible: text.length > 0
                                horizontalAlignment: Text.AlignRight
                                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                color: row.fromSelf ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                                opacity: 0.7
                            }
                        }
                    }
                }

                Connections {
                    target: backend
                    function onMessagesChanged() { chatView.scrollToLatest() }
                }

                Kirigami.FormLayout {
                    Layout.fillWidth: true
                    Layout.margins: Kirigami.Units.gridUnit
                    QQC2.TextField {
                        Kirigami.FormData.label: "Message"
                        Layout.fillWidth: true
                        placeholderText: backend.activeFriendId ? "Message…" : "Pick a friend first"
                        enabled: !!backend.activeFriendId
                        Keys.onPressed: function(event) {
                            if (event.key === Qt.Key_V &&
                                (event.modifiers & Qt.ControlModifier) &&
                                backend.pasteImage()) {
                                event.accepted = true
                            }
                        }
                        onAccepted: {
                            backend.sendMessage(text)
                            text = ""
                        }
                    }
                }
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

    QQC2.Popup {
        id: imagePopup
        parent: QQC2.Overlay.overlay
        anchors.centerIn: parent
        modal: true
        focus: true
        padding: Kirigami.Units.smallSpacing
        property string imageUrl: ""
        width: Math.max(240, Math.min(root.width * 0.92,
            fullImage.implicitWidth + padding * 2))
        height: Math.max(160, Math.min(root.height * 0.92,
            fullImage.implicitHeight + padding * 2))
        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
        background: Rectangle {
            color: Kirigami.Theme.backgroundColor
            radius: Kirigami.Units.smallSpacing
            border.color: Kirigami.Theme.disabledTextColor
        }
        contentItem: Flickable {
            id: imageViewport
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            contentWidth: Math.max(width, fullImage.width)
            contentHeight: Math.max(height, fullImage.height)
            Image {
                id: fullImage
                source: imagePopup.imageUrl
                width: implicitWidth
                height: implicitHeight
                x: Math.max(0, (imageViewport.contentWidth - width) / 2)
                y: Math.max(0, (imageViewport.contentHeight - height) / 2)
                asynchronous: true
                cache: true
            }
        }
    }

    QQC2.Menu {
        id: imageMenu
        property string imageUrl: ""
        QQC2.MenuItem {
            text: "Copy image URL"
            onTriggered: backend.copyImageUrl(imageMenu.imageUrl)
        }
        QQC2.MenuItem {
            text: "Copy image"
            onTriggered: backend.copyImage(imageMenu.imageUrl)
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
}
