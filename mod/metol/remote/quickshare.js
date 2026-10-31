/* One gallery and one camera service; no browser-side photo relay. */
(function () {
    'use strict';
    function QuickShare(gallery) {
        this.gallery = gallery;
        this.available = false;
        this.devices = {};
        this.task = null;
        this.sequence = 0;
        this.pending = false;
        this.message = '正在读取机身能力…';
        var self = this;
        $.getJSON('/api/v1/camera/info').done(function (info) {
            self.available = !!(info.capabilities && info.capabilities.quickshare_native);
            self.message = self.available ? '选择图库文件和接收设备后发送' : '机身 Quick Share 尚未部署';
            self.refresh();
        }).fail(function () { self.message = '无法读取机身能力';self.refresh(); });
        this.poll();
        this.timer = setInterval(function () {
            if (self.task && self.task.running || window.app && app.activeTab === '#gallery') self.poll();
        }, 1000);
    }
    QuickShare.prototype.mount = function ($bar) {
        var self = this;
        this.panel = $('<section class="nx-quickshare" aria-label="Quick Share"></section>');
        var $row = $('<div class="nx-quickshare-row"></div>');
        $row.append('<strong>Quick Share（Google）</strong>');
        this.discover = $('<button type="button" class="btn btn-xs btn-default">发现设备</button>');
        this.device = $('<select class="nx-quickshare-device" aria-label="接收设备"><option value="">选择接收设备</option></select>');
        this.format = $('<select class="nx-gallery-format" aria-label="Quick Share 文件格式"></select>').hide();
        this.formatKey = null;
        this.send = $('<button type="button" class="btn btn-xs btn-success">发送所选文件</button>');
        this.cancel = $('<button type="button" class="btn btn-xs btn-default">取消任务</button>');
        this.file = $('<div class="nx-quickshare-file"></div>');
        this.status = $('<div class="nx-quickshare-status" role="status" aria-live="polite"></div>');
        this.pin = $('<strong class="nx-quickshare-pin"></strong>');
        this.progress = $('<progress class="nx-quickshare-progress" aria-label="传输字节进度"></progress>');
        this.bytes = $('<span class="nx-quickshare-bytes"></span>');
        this.logs = $('<pre class="nx-quickshare-logs"></pre>');
        var $details = $('<details><summary>任务日志</summary></details>').append(this.logs);
        $row.append(this.discover, this.device, this.format, this.send, this.cancel);
        this.panel.append($row, this.file, this.status, this.pin, this.progress, this.bytes, $details);
        $bar.append(this.panel);
        this.discover.on('click', function () {
            self.devices = {};self.renderDevices();
            self.start('discover', {duration: 15});
        });
        this.device.on('change', function () { self.refresh(); });
        this.format.on('change', function () { self.refresh(); });
        this.send.on('click', function () {
            var files = self.selectedFiles(), device = self.devices[self.device.val()];
            if (!files.length || files.length > 16 || !device || self.pending || self.task && self.task.running) return;
            var data = {endpoint: device.ip + ':' + device.port, name: device.name, path_count: files.length};
            for (var i = 0; i < files.length; i++) {
                var path = Gallery.dirRelPath(files[i].url);
                if (path === null) { self.message = '所选文件不在机身图库中';self.refresh();return; }
                data['path' + i] = path;
            }
            self.start('send', data);
        });
        this.cancel.on('click', function () { self.start('cancel', {}); });
        this.renderDevices();this.refresh();
    };
    QuickShare.prototype.selectedItems = function () {
        var keys = Object.keys(this.gallery.selection).filter(function (key) { return !!this.gallery.selection[key]; }, this);
        return keys.map(function (key) { return this.gallery.media[key]; }, this).filter(Boolean);
    };
    QuickShare.prototype.selectedFiles = function () {
        var items = this.selectedItems(), format = this.format.val(), files = [], seen = Object.create(null), gallery = this.gallery;
        if (!format && items.some(function (item) { return item.variants && item.variants.jpeg && item.variants.raw; })) return [];
        items.forEach(function (item) {
            gallery.filesForFormat(item, format).forEach(function (file) {
                if (!seen[file.url]) { seen[file.url] = true;files.push(file); }
            });
        });
        return files;
    };
    QuickShare.prototype.renderDevices = function () {
        if (!this.device) return;
        var selected = this.device.val(), self = this;
        this.device.empty().append($('<option value=""></option>').text('选择接收设备'));
        Object.keys(this.devices).forEach(function (key) {
            var device = self.devices[key];
            self.device.append($('<option></option>').val(key).text(device.name + ' · ' + key));
        });
        if (this.devices[selected]) this.device.val(selected);
    };
    QuickShare.prototype.refresh = function () {
        if (!this.panel) return;
        var items = this.selectedItems(), key = items.map(function (item) { return item.url; }).join('\n');
        var paired = items.some(function (item) { return item.variants && item.variants.jpeg && item.variants.raw; });
        if (key !== this.formatKey) {
            this.formatKey = key;
            this.format.empty().append('<option value="">选择发送格式</option><option value="jpeg">JPEG</option><option value="raw">RAW</option><option value="both">JPEG + RAW</option>');
        }
        var files = this.selectedFiles(), running = !!(this.task && this.task.running), busy = running || this.pending;
        this.format.toggle(!!paired).prop('disabled', busy);
        this.file.text(files.length > 16 ? '已选 ' + files.length + ' 个文件，每次最多发送 16 个' : files.length ? '所选文件（' + files.length + '）：' + files.map(function (file) { return file.name; }).join('、') : paired ? '所选照片包含 RAW + JPEG，请选择发送格式' : '使用上方“选择”，勾选照片或文件');
        this.send.text('发送所选文件' + (files.length ? '（' + files.length + '）' : ''));
        this.discover.prop('disabled', !this.available || busy);
        this.device.prop('disabled', !this.available || busy);
        this.send.prop('disabled', !this.available || busy || !files.length || files.length > 16 || !this.devices[this.device.val()]);
        this.cancel.prop('disabled', !running || this.pending || !!this.task.cancel_requested);
        this.status.text(this.message);
        this.pin.text(this.pinCode ? 'PIN ' + this.pinCode : '').toggle(!!this.pinCode);
        var total = Number(this.totalBytes), ack = Number(this.ackBytes), sent = Number(this.sentBytes);
        this.progress.toggle(total > 0);
        this.bytes.text(total > 0 ? '已发送 ' + sent.toLocaleString() + ' / ' + total.toLocaleString() + ' 字节；' + (this.completionBasis === 'safe_disconnect_ack' ? '接收端已确认结束' : '接收端逐文件确认 ' + ack.toLocaleString() + ' 字节') : '');
        if (total > 0) this.progress.attr('max', total).val(sent);
        this.logs.text(this.task && this.task.stderr || '');
    };
    QuickShare.prototype.accept = function (task) {
        if (!this.task || task.task_id !== this.task.task_id) {
            this.sequence = 0;this.pinCode = null;this.totalBytes = 0;this.sentBytes = 0;this.ackBytes = 0;this.transferState = null;this.completionBasis = null;
        }
        this.task = task;
        var self = this;
        (task.events || []).forEach(function (entry) {
            if (entry.sequence <= self.sequence) return;
            self.sequence = entry.sequence;
            var event;
            try { event = JSON.parse(entry.line); } catch (error) { return; }
            if (event.event === 'discovered' && event.endpoint) {
                var endpoint = event.endpoint, key = endpoint.ip + ':' + endpoint.port;
                if (!/^\d+\.\d+\.\d+\.\d+$/.test(String(endpoint.ip)) || !endpoint.name) return;
                if (endpoint.present === false) delete self.devices[key];else self.devices[key] = endpoint;
                self.renderDevices();
            } else if (event.event === 'transfer' && event.message) {
                var message = event.message, meta = message.meta || {};
                self.transferState = message.state;
                if (meta.pin_code != null) self.pinCode = String(meta.pin_code);
                if (meta.total_bytes != null) self.totalBytes = meta.total_bytes;
                if (meta.ack_bytes != null) self.ackBytes = meta.ack_bytes;
                if (meta.sent_bytes != null) self.sentBytes = meta.sent_bytes;
                if (meta.completion_basis != null) self.completionBasis = meta.completion_basis;
            }
        });
        if (task.running) {
            this.message = task.cancel_requested ? '正在取消任务…' : task.operation === 'discover' ? '正在发现附近设备…' : this.phase();
        } else if (task.state === 'idle') {
            // Preserve the capability/selection prompt until a real task exists.
        } else if (task.state === 'cancelled') this.message = '任务已取消';
        else if (task.signal != null) this.message = '任务被信号 ' + task.signal + ' 终止';
        else if (task.exit_code !== 0) this.message = '任务失败，退出码 ' + task.exit_code + '；详情见任务日志';
        else if (task.operation === 'discover') this.message = '发现结束，当前找到 ' + Object.keys(this.devices).length + ' 个设备';
        else this.message = this.transferState === 'Finished' ? '文件发送完成' : '进程已结束，未收到传输完成事件';
        this.refresh();
    };
    QuickShare.prototype.phase = function () {
        var phases = {SentUkeyClientInit: '正在建立加密连接…', SentIntroduction: '等待接收设备确认…',
            WaitingForUserConsent: '等待接收设备确认…', SendingFiles: '正在发送文件…',
            AwaitingReceiverAck: '文件已发出，等待接收设备确认…',
            Finished: '传输完成，等待进程退出…', Rejected: '接收设备已拒绝', Disconnected: '连接已断开', Cancelled: '传输已取消'};
        return phases[this.transferState] || (this.transferState ? '协议阶段：' + this.transferState : '正在启动发送任务…');
    };
    QuickShare.prototype.start = function (operation, data) {
        var self = this;
        if (this.pending) return;
        this.pending = true;this.refresh();
        $.ajax({url: '/api/v1/quickshare/' + operation, method: 'POST', data: data, dataType: 'json', timeout: 10000})
            .done(function (task) { self.accept(task); })
            .fail(function (xhr) { self.message = xhr.responseJSON && xhr.responseJSON.error || '机身任务请求失败'; })
            .always(function () { self.pending = false;self.refresh(); });
    };
    QuickShare.prototype.poll = function () {
        var self = this;
        if (this.reading || this.pending) return;
        this.reading = true;
        $.ajax({url: '/api/v1/quickshare/status', dataType: 'json', cache: false, timeout: 5000})
            .done(function (task) { self.accept(task); })
            .fail(function () { self.message = '无法读取机身任务状态';self.refresh(); })
            .always(function () { self.reading = false; });
    };
    var buildToolbar = Gallery.prototype.buildToolbar;
    Gallery.prototype.buildToolbar = function () {
        var bar = buildToolbar.apply(this, arguments);
        if (!this.quickshare) this.quickshare = new QuickShare(this);
        this.quickshare.mount(bar);return bar;
    };
    var updateCount = Gallery.prototype.updateDownloadCount;
    Gallery.prototype.updateDownloadCount = function () {
        updateCount.apply(this, arguments);
        if (this.quickshare) this.quickshare.refresh();
    };
})();
