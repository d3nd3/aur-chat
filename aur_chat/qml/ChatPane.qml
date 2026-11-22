import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

ColumnLayout {
    spacing: 0
    readonly property int chatSize: backend.chatFontSize > 0
        ? backend.chatFontSize : Kirigami.Theme.defaultFont.pixelSize

    // Vibrant name-chip colours, picked per sender so each side of the dialog
    // gets its own hue (white bold text stays readable on all of them).
    function chatNameColor(name: string) : color {
        const colors = ["#e05555", "#e08a3c", "#4caf6d", "#26a69a",
                        "#4a90d9", "#9b6fd4", "#d964a3", "#3fb3c9"]
        let h = 0
        for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 997
        return colors[h % colors.length]
    }

    Flow {
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing
        visible: backend.chatTabs.length > 0
        Repeater {
            model: backend.chatTabs
            RowLayout {
                required property var modelData
                spacing: 0
                QQC2.Button {
                    id: tabBtn
                    highlighted: modelData.steamId === backend.activeFriendId
                    flat: !highlighted
                    onClicked: backend.pickFriend(modelData.steamId)
                    QQC2.ToolTip.visible: hovered && (modelData.lastSeen.length > 0 || modelData.richText.length > 0)
                    QQC2.ToolTip.text: [modelData.lastSeen, modelData.richText].filter(Boolean).join("\n")
                    // Neutral wash instead of the style's highlight: stays readable
                    // on every theme (some paint highlighted buttons dark).
                    background: Rectangle {
                        radius: Kirigami.Units.smallSpacing / 2
                        color: tabBtn.highlighted
                            ? Qt.alpha(Kirigami.Theme.textColor, 0.14)
                            : tabBtn.hovered ? Qt.alpha(Kirigami.Theme.textColor, 0.06) : "transparent"
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                    // friend-list visuals on two lines to keep tabs narrow
                    contentItem: ColumnLayout {
                        spacing: 1
                        RowLayout {
                            spacing: 4
                            Rectangle {
                                Layout.alignment: Qt.AlignVCenter
                                width: Kirigami.Units.gridUnit * 0.5
                                height: width
                                radius: width / 2
                                color: modelData.statusColor
                            }
                            Text {
                                text: modelData.name
                                font.bold: modelData.unread > 0 || tabBtn.highlighted
                                color: Kirigami.Theme.textColor
                                Layout.alignment: Qt.AlignVCenter
                            }
                            Rectangle {
                                visible: modelData.unread > 0
                                radius: height / 2
                                Layout.alignment: Qt.AlignVCenter
                                implicitWidth: Math.max(unreadText.implicitWidth + 8, implicitHeight)
                                implicitHeight: unreadText.implicitHeight + 2
                                color: Kirigami.Theme.highlightColor
                                Text {
                                    id: unreadText
                                    anchors.centerIn: parent
                                    text: modelData.unread
                                    color: Kirigami.Theme.highlightedTextColor
                                    font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                }
                            }
                        }
                        RowLayout {
                            spacing: 4
                            Text {
                                text: modelData.subtitle
                                visible: text.length > 0
                                font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                color: tabBtn.highlighted
                                    ? Kirigami.Theme.textColor : Kirigami.Theme.disabledTextColor
                                opacity: tabBtn.highlighted ? 0.85 : 1
                                Layout.alignment: Qt.AlignVCenter
                            }
                            Rectangle {
                                visible: (modelData.device || "").length > 0
                                radius: height / 2
                                Layout.alignment: Qt.AlignVCenter
                                color: tabBtn.highlighted
                                    ? Qt.alpha(Kirigami.Theme.textColor, 0.1)
                                    : Kirigami.Theme.alternateBackgroundColor
                                width: devRow.implicitWidth + 6
                                height: Math.max(devIcon.height, devText.implicitHeight) + 3
                                Row {
                                    id: devRow
                                    anchors.centerIn: parent
                                    spacing: 2
                                    Kirigami.Icon {
                                        id: devIcon
                                        source: modelData.deviceIcon
                                        width: 11
                                        height: 11
                                        visible: source.length > 0
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Text {
                                        id: devText
                                        text: modelData.device
                                        font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                                        color: tabBtn.highlighted
                                            ? Kirigami.Theme.textColor
                                            : Kirigami.Theme.disabledTextColor
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                            }
                        }
                    }
                    TapHandler {
                        acceptedButtons: Qt.RightButton
                        onTapped: {
                            tabMenu.steamId = modelData.steamId
                            tabMenu.tabAlias = modelData.alias || ""
                            tabMenu.popup(tabBtn, point.position.x, point.position.y)
                        }
                    }
                }
                QQC2.ToolButton {
                    Layout.alignment: Qt.AlignVCenter
                    icon.name: "window-close"
                    onClicked: backend.closeChat(modelData.steamId)
                }
            }
        }
    }

    Kirigami.InlineMessage {
        id: authBanner
        Layout.fillWidth: true
        type: Kirigami.MessageType.Error
        text: "Not logged in — run `aurelia login` (or `aurelia login --qr`), then F5."
        visible: backend.authRequired
    }

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: true

    ListView {
        id: chatView
        objectName: "chatView"
        anchors.fill: parent
        clip: true
        spacing: 1
        boundsBehavior: Flickable.StopAtBounds
        QQC2.ScrollBar.vertical: QQC2.ScrollBar {
            id: chatBar
            width: 6
            onPressedChanged: pinned = pressed ? false : chatView.atYEnd
            contentItem: Rectangle {
                radius: width / 2
                color: Kirigami.Theme.disabledTextColor
                opacity: chatBar.active ? 0.9 : 0.4
            }
        }
        model: backend.chatModel
        property bool pinned: true  // follow new messages only while the user is at the bottom
        onMovingChanged: if (!moving) pinned = atYEnd
        WheelHandler {  // wheel drives the scroll itself; the view's own adjustments must not unpin
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: function(wheel) {
                chatView.contentY -= wheel.angleDelta.y * 0.4  // ~3 lines per wheel notch
                chatView.returnToBounds()
                Qt.callLater(function() { chatView.pinned = chatView.atYEnd })
            }
        }
        function pageScroll(dir) {  // PgUp/PgDn from the input
            cancelFlick()
            contentY += dir * (height - Kirigami.Units.largeSpacing)
            returnToBounds()
            pinned = atYEnd
        }
        TapHandler {
            acceptedButtons: Qt.RightButton
            onTapped: {
                const p = point.position
                const row = chatView.itemAtIndex(chatView.indexAt(p.x, p.y + chatView.contentY))
                chatMenu.imageUrl = row ? row.imageUrl : ""
                chatMenu.rawText = row ? row.rawText : ""
                chatMenu.popup()
            }
        }
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
            height: bubble.y + bubble.height + 1
            readonly property bool fromSelf: model.fromSelf
            readonly property string msg: model.html
            readonly property string time: model.time
            readonly property string imageUrl: model.imageUrl || ""
            readonly property string stickerUrl: model.stickerUrl || ""
            readonly property string stickerName: model.stickerName || ""
            // Re-resolves when mediaRev bumps (media cache warmed): rows created
            // before a sticker download landed would otherwise keep a frozen remote APNG URL.
            readonly property string mediaSrc: (backend.mediaRev, row.imageUrl !== ""
                ? row.imageUrl : row.stickerName !== "" ? backend.stickerIcon(row.stickerName) : "")
            readonly property string rawText: model.text || ""
            readonly property string sender: model.sender || ""
            readonly property bool showName: !!model.showName && sender !== ""
            readonly property string videoId: model.videoId || ""
            readonly property bool isVideo: videoId !== ""
            readonly property real thumbH: bubble.width * 9 / 16
            readonly property real pad: 2  // utilization first: minimal chrome, max characters
            readonly property real sidePad: Kirigami.Units.smallSpacing
            readonly property real maxW: chatView.width * 0.88
            readonly property real maxImageW: Math.max(1, Math.min(
                row.maxW, chatView.width - row.sidePad * 2
            ))
            // the stamp flows inline after the text instead of costing its own line
            readonly property string stampHtml: media.visible || row.time === "" ? ""
                : ' <span style="color:' +
                  (fromSelf ? Kirigami.Theme.textColor : Kirigami.Theme.disabledTextColor) +
                  ';font-size:' + Math.max(8, Math.round(chatSize * 0.85)) + 'px">· ' + row.time + '</span>'
            readonly property bool hasImage: (row.imageUrl !== "" || row.stickerUrl !== "") &&
                media.status === Image.Ready && media.implicitWidth > 0
            readonly property real imageW: row.hasImage
                ? Math.min(media.implicitWidth, row.maxImageW) : 0
            readonly property real imageH: row.hasImage
                ? media.implicitHeight * row.imageW / media.implicitWidth : 0

            Rectangle {
                id: senderChip
                x: row.sidePad
                visible: row.showName
                width: senderLabel.implicitWidth + row.pad * 5
                height: senderLabel.implicitHeight + row.pad * 2
                radius: height / 2
                color: chatNameColor(row.sender)
                Text {
                    id: senderLabel
                    anchors.centerIn: parent
                    text: row.sender
                    font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                    font.bold: true
                    color: "#ffffff"
                }
            }
            Rectangle {
                id: bubble
                y: senderChip.visible ? senderChip.height + 1 : 0
                anchors.left: parent.left
                anchors.leftMargin: row.sidePad
                width: row.hasImage
                    ? row.imageW + row.pad * 2
                    : Math.min(row.maxW, chatView.width - row.sidePad * 2)
                height: (video.visible ? video.height
                    : media.visible ? row.imageH : body.paintedHeight) +
                    (stamp.visible ? stamp.paintedHeight + row.pad : 0) +
                    row.pad * 2
                radius: 3
                // distinct sides: ours gets a highlight tint, theirs stays neutral
                color: row.fromSelf
                    ? Qt.tint(Kirigami.Theme.alternateBackgroundColor,
                              Qt.alpha(Kirigami.Theme.highlightColor, 0.4))
                    : Kirigami.Theme.alternateBackgroundColor
                AnimatedImage {
                    id: media
                    x: row.pad
                    y: row.pad
                    width: row.imageW
                    height: row.imageH
                    source: row.mediaSrc
                    fillMode: AnimatedImage.PreserveAspectFit
                    asynchronous: true
                    // animate only while on screen, throttled by the settings menu;
                    // off-viewport rows stay paused and speed<1 decodes fewer fps
                    playing: row.hasImage && backend.animSpeed > 0 && (function() {
                        const top = row.y + bubble.y + media.y
                        return top + height > chatView.contentY
                            && top < chatView.contentY + chatView.height
                    })()
                    speed: backend.animSpeed
                    visible: row.hasImage
                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            imagePopup.imageUrl = row.mediaSrc
                            imagePopup.open()
                        }
                    }
                }
                Item {
                    id: video
                    x: row.pad
                    y: row.pad
                    visible: row.isVideo
                    width: bubble.width - row.pad * 2
                    height: videoFace.height

                    Rectangle {
                        id: videoFace
                        width: parent.width
                        height: videoThumb.height + videoTitle.paintedHeight +
                                videoMeta.paintedHeight + row.pad * 3
                        radius: 3
                        color: Qt.lighter(Kirigami.Theme.alternateBackgroundColor, 1.08)

                        Item {
                            id: videoThumb
                            width: parent.width
                            height: row.thumbH
                            clip: true
                            Image {
                                anchors.fill: parent
                                fillMode: Image.PreserveAspectCrop
                                source: row.videoId
                                    ? "https://img.youtube.com/vi/" + row.videoId + "/mqdefault.jpg" : ""
                                asynchronous: true
                            }
                            Kirigami.Icon {
                                anchors.centerIn: parent
                                source: "media-playback-start"
                                width: Kirigami.Units.gridUnit * 1.6
                                height: width
                            }
                        }
                        Text {
                            id: videoTitle
                            x: row.pad
                            y: videoThumb.height + row.pad
                            width: parent.width - row.pad * 2
                            text: {
                                const info = backend.videoInfo[row.videoId]
                                return (info && info.title) || row.videoId
                            }
                            font.bold: true
                            font.pixelSize: chatSize
                            elide: Text.ElideRight
                            color: Kirigami.Theme.textColor
                        }
                        Text {
                            id: videoMeta
                            x: row.pad
                            y: videoTitle.y + videoTitle.paintedHeight + 1
                            width: parent.width - row.pad * 2
                            text: {
                                const info = backend.videoInfo[row.videoId]
                                return info && info.author ? info.author + " · YouTube" : "YouTube"
                            }
                            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                            elide: Text.ElideRight
                            color: Kirigami.Theme.disabledTextColor
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: Qt.openUrlExternally(
                                "https://www.youtube.com/watch?v=" + row.videoId)
                        }
                    }
                }
                TextEdit {
                    id: body
                    x: row.pad
                    y: row.pad
                    width: parent.width - row.pad * 2
                    text: row.msg + row.stampHtml
                    textFormat: TextEdit.RichText
                    textMargin: 0
                    font.pixelSize: chatSize
                    visible: !media.visible && !video.visible
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    color: Kirigami.Theme.textColor
                }
                Text {
                    id: stamp
                    x: row.pad
                    y: media.y + media.height + row.pad
                    width: parent.width - row.pad * 2
                    text: row.time
                    visible: media.visible && text.length > 0  // text stamps are inline now
                    horizontalAlignment: Text.AlignRight
                    font.pixelSize: Math.max(8, Math.round(chatSize * 0.85))
                    color: row.fromSelf ? Kirigami.Theme.textColor : Kirigami.Theme.disabledTextColor
                    opacity: 0.7
                }
            }
        }
    }

    // Jump-to-latest: a sibling of the ListView (children of a Flickable scroll
    // away with the content), shown only while scrolled up.
    QQC2.ToolButton {
        id: toBottom
        objectName: "toBottom"
        visible: !chatView.atYEnd  // ground truth: shows however you scroll
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Kirigami.Units.largeSpacing
        z: 5
        width: 40
        height: 40
        hoverEnabled: true
        opacity: hovered ? 1 : 0.9
        QQC2.ToolTip.visible: hovered
        QQC2.ToolTip.text: "Jump to latest"
        background: Rectangle {  // fixed chip blue: reads on any theme, any background
            radius: width / 2
            color: toBottom.hovered ? "#5fa8e8" : "#4a90d9"
            Rectangle {  // drawn chevron: renders regardless of the font's glyphs
                anchors.centerIn: parent
                width: 16
                height: 15
                Rectangle { width: 3; height: 13; color: "white"; rotation: -40; x: 3; y: 1 }
                Rectangle { width: 3; height: 13; color: "white"; rotation: 40; x: 10; y: 1 }
            }
        }
        onClicked: {
            chatView.cancelFlick()  // a settling flick would fight the jump
            chatView.pinned = true
            chatView.positionViewAtEnd()  // canonical end, unlike a manual contentY write
            chatView.scrollToLatest()  // re-anchor once pending layout settles
        }
    }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.margins: 2
        spacing: 0
        QQC2.TextArea {
            id: input
            objectName: "chatInput"
            Layout.fillWidth: true
            font.pixelSize: chatSize
            wrapMode: QQC2.TextArea.Wrap
            implicitHeight: Math.min(contentHeight + topPadding + bottomPadding, chatSize * 6)
            leftPadding: 6
            rightPadding: 6
            topPadding: 3
            bottomPadding: 3
            placeholderText: backend.activeFriendId ? "Message…" : "Pick a friend first"
            enabled: !!backend.activeFriendId
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown) {
                    chatView.pageScroll(event.key === Qt.Key_PageDown ? 1 : -1)
                    event.accepted = true
                } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    if (event.modifiers & Qt.ShiftModifier)
                        return  // Shift+Enter: newline, not send
                    event.accepted = true
                    backend.sendMessage(text)
                    text = ""
                } else if (event.key === Qt.Key_V &&
                    (event.modifiers & Qt.ControlModifier) &&
                    backend.pasteImage()) {
                    event.accepted = true
                }
            }
        }
        QQC2.ToolButton {
            id: emoBtn
            objectName: "emoBtn"
            enabled: input.enabled
            icon.name: "face-smile"
            QQC2.ToolTip.text: "Emoticons & stickers"
            QQC2.ToolTip.visible: hovered
            onClicked: emoPopup.opened ? emoPopup.close() : emoPopup.open()
        }
    }
    QQC2.Popup {
        id: emoPopup
        property bool stickers: false
        parent: emoBtn
        x: emoBtn.width - width  // keep the right edge inside the pane
        y: -height - 4
        width: grid.cellWidth * 8 + padding * 2
        height: contentItem.implicitHeight + padding * 2
        padding: Kirigami.Units.smallSpacing
        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
        background: Rectangle {
            color: Kirigami.Theme.backgroundColor
            radius: Kirigami.Units.smallSpacing
            border.color: Kirigami.Theme.disabledTextColor
        }
        contentItem: Column {
            spacing: Kirigami.Units.smallSpacing
            Row {
                spacing: Kirigami.Units.smallSpacing
                Repeater {
                    model: ["Emoticons", "Stickers"]
                    QQC2.Button {
                        required property int index
                        required property string modelData
                        text: modelData
                        flat: true
                        highlighted: emoPopup.stickers === (index === 1)
                        onClicked: emoPopup.stickers = index === 1
                    }
                }
            }
            GridView {
                id: grid
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                cellWidth: emoPopup.stickers ? 64 : 26
                cellHeight: cellWidth
                width: cellWidth * 8
                height: Math.min(contentHeight, cellHeight * 6)
                model: emoPopup.stickers ? backend.stickers : backend.emoticons
                delegate: Item {
                    id: emoCell
                    width: grid.cellWidth
                    height: grid.cellHeight
                    required property var modelData
                    Image {
                        anchors.centerIn: parent
                        visible: !emoPopup.stickers
                        source: visible ? backend.emoticonIcon(emoCell.modelData) : ""
                        sourceSize: Qt.size(18, 18)
                        asynchronous: true
                        cache: true
                    }
                    AnimatedImage {
                        anchors.centerIn: parent
                        visible: emoPopup.stickers
                        width: height  // stickers animate (APNG → GIF conversion)
                        height: 56
                        source: (backend.mediaRev, emoPopup.stickers
                                 ? backend.stickerIcon(emoCell.modelData) : "")
                        asynchronous: true
                        cache: true
                        playing: emoPopup.opened && backend.animSpeed > 0
                        speed: backend.animSpeed
                    }
                    MouseArea {
                        id: emoArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (emoPopup.stickers) {
                                backend.sendSticker(emoCell.modelData)
                            } else {
                                input.insert(input.cursorPosition, ":" + emoCell.modelData + ":")
                                input.forceActiveFocus()
                            }
                            emoPopup.close()
                        }
                    }
                    QQC2.ToolTip.visible: emoArea.containsMouse
                    QQC2.ToolTip.text: emoPopup.stickers ? emoCell.modelData
                                                         : ":" + emoCell.modelData + ":"
                }
            }
        }
    }

    // Image viewer: its own window sized to the image's real dimensions — no
    // viewport cap, no panning. Clicking away (losing focus) or Esc closes it.
    Window {
        id: imagePopup
        property string imageUrl: ""
        property bool wasActive: false
        title: "Image"
        color: Kirigami.Theme.backgroundColor
        visible: false
        width: fullImage.implicitWidth
        height: fullImage.implicitHeight
        function open() {
            // re-bind: a manual resize would otherwise freeze the size for good
            width = Qt.binding(() => fullImage.implicitWidth)
            height = Qt.binding(() => fullImage.implicitHeight)
            visible = true
            requestActivate()
        }
        onActiveChanged: if (active) wasActive = true; else if (wasActive) close()
        AnimatedImage {
            id: fullImage
            anchors.fill: parent
            fillMode: AnimatedImage.PreserveAspectFit  // stickers animate full-size too
            source: imagePopup.imageUrl
            asynchronous: true
            cache: true
            playing: imagePopup.visible && backend.animSpeed > 0
            speed: backend.animSpeed
        }
        Shortcut {
            sequence: "Escape"
            onActivated: imagePopup.close()
        }
    }

    QQC2.Menu {
        id: chatMenu
        property string imageUrl: ""
        property string rawText: ""
        QQC2.MenuItem {
            text: "Copy image"
            visible: chatMenu.imageUrl !== ""
            onTriggered: backend.copyImage(chatMenu.imageUrl)
        }
        QQC2.MenuItem {
            text: "Copy image URL"
            visible: chatMenu.imageUrl !== ""
            onTriggered: backend.copyImageUrl(chatMenu.imageUrl)
        }
        QQC2.MenuItem {
            text: "Copy message"
            visible: chatMenu.rawText !== ""
            onTriggered: backend.copyText(chatMenu.rawText)
        }
        QQC2.MenuItem {
            text: "Copy all messages"
            onTriggered: backend.copyText(backend.transcript())
        }
        QQC2.MenuItem {
            text: "Select messages…"
            onTriggered: {
                transcriptArea.text = backend.transcript()
                transcript.open()
            }
        }
    }

    QQC2.Popup {
        id: transcript
        parent: QQC2.Overlay.overlay
        anchors.centerIn: parent
        modal: true
        focus: true
        width: Math.min(640, parent.width * 0.9)
        height: parent.height * 0.75
        padding: Kirigami.Units.smallSpacing
        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
        background: Rectangle {
            color: Kirigami.Theme.backgroundColor
            radius: Kirigami.Units.smallSpacing
            border.color: Kirigami.Theme.disabledTextColor
        }
        contentItem: QQC2.ScrollView {
            QQC2.TextArea {
                id: transcriptArea
                readOnly: true
                wrapMode: TextEdit.Wrap
                selectByMouse: true
                font.pixelSize: chatSize
                color: Kirigami.Theme.textColor
                onActiveFocusChanged: if (activeFocus) selectAll()
            }
        }
    }

    QQC2.Menu {
        id: tabMenu
        property string steamId: ""
        property string tabAlias: ""
        QQC2.MenuItem { text: "View Steam profile"; onTriggered: backend.openProfile(tabMenu.steamId) }
        QQC2.MenuItem { text: "Copy Steam ID"; onTriggered: backend.copyText(tabMenu.steamId) }
        QQC2.MenuItem {
            text: "Previous names…"
            onTriggered: {
                prevArea.text = backend.friendNameHistory(tabMenu.steamId).join("\n")
                prevNames.open()
            }
        }
        QQC2.MenuItem {
            text: "Assign alias…"
            onTriggered: { aliasField.text = tabMenu.tabAlias; aliasPopup.open() }
        }
    }

    QQC2.Popup {
        id: aliasPopup
        parent: QQC2.Overlay.overlay
        anchors.centerIn: parent
        modal: true
        focus: true
        padding: Kirigami.Units.smallSpacing
        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
        background: Rectangle {
            color: Kirigami.Theme.backgroundColor
            radius: Kirigami.Units.smallSpacing
            border.color: Kirigami.Theme.disabledTextColor
        }
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.smallSpacing
            QQC2.TextField {
                id: aliasField
                Layout.preferredWidth: Kirigami.Units.gridUnit * 18
                placeholderText: "Alias · (real name) is shown after it · empty removes it"
                onAccepted: aliasSave.clicked()
            }
            RowLayout {
                Layout.fillWidth: true
                QQC2.Button { text: "Cancel"; onClicked: aliasPopup.close() }
                Item { Layout.fillWidth: true }
                QQC2.Button {
                    id: aliasSave
                    text: "Save"
                    onClicked: { backend.setAlias(tabMenu.steamId, aliasField.text); aliasPopup.close() }
                }
            }
        }
    }

    QQC2.Popup {
        id: prevNames
        parent: QQC2.Overlay.overlay
        anchors.centerIn: parent
        modal: true
        padding: Kirigami.Units.smallSpacing
        width: Math.min(360, parent.width * 0.8)
        height: Math.min(320, parent.height * 0.6)
        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
        background: Rectangle {
            color: Kirigami.Theme.backgroundColor
            radius: Kirigami.Units.smallSpacing
            border.color: Kirigami.Theme.disabledTextColor
        }
        contentItem: QQC2.ScrollView {
            QQC2.TextArea {
                id: prevArea
                readOnly: true
                wrapMode: TextEdit.Wrap
                selectByMouse: true
            }
        }
    }
}
