/* Catalogue and staging for the existing native LUT hook. */
(function () {
    'use strict';
    var initialize = Settings.init;
    Settings.init = function () {
        initialize.apply(this, arguments);
        if ($('#nxLooksSettings').length) return;
        var modal = $('<div id="nxLooksSettings" class="modal fade" tabindex="-1" role="dialog" aria-labelledby="nxLooksTitle"></div>');
        var content = $('<div class="modal-dialog"><div class="modal-content"><div class="modal-header"><button type="button" class="close" data-dismiss="modal" aria-label="关闭">&times;</button><h4 class="modal-title" id="nxLooksTitle">LUT 风格</h4></div><div class="modal-body"></div><div class="modal-footer"><span class="pull-left">仅当前相机会话</span><button type="button" class="btn btn-default" data-dismiss="modal">关闭</button></div></div></div>');
        var body = content.find('.modal-body');
        var choice = $('<select id="nxLookChoice" class="form-control"><option value="">未读取</option></select>');
        var apply = $('<button type="button" class="btn btn-primary">确认并暂存</button>');
        var strength = $('<input id="nxLookStrength" type="range" min="0" max="100" step="1" value="100">');
        var strengthValue = $('<output for="nxLookStrength">100%</output>');
        var strengthGroup = $('<div class="form-group nx-look-strength"></div>').append(
            $('<label for="nxLookStrength">LUT 强度</label>'), strengthValue, strength).hide();
        var stock = $('<button type="button" class="btn btn-default" style="margin-left:8px">恢复原厂</button>');
        var refresh = $('<button type="button" class="btn btn-default">读取／刷新状态</button>');
        var sync = $('<button type="button" class="btn btn-default" style="margin-left:8px">同步本地 LUT 库</button>').hide();
        var selected = $('<p style="margin-top:12px"></p>');
        var firmware = $('<p></p>');
        var status = $('<p role="status" aria-live="polite"></p>');
        body.append($('<div class="form-group"></div>').append($('<label for="nxLookChoice">已部署风格</label>'), choice), strengthGroup, apply, stock, selected, firmware,
            $('<p>暂存后等待原厂下一次 LUT 调用。实拍效果未验证；录制画面回放不显示 ISP 风格效果。</p>'), status, refresh, sync);
        modal.append(content).appendTo(document.body);
        var entry = $('<a href="#">LUT 风格…</a>');
        $('#settingsMenu').append($('<li></li>').append(entry));
        var busy = false, ready = false, snapshot = null, canSync = false;
        function update() {
            var allowed = snapshot && snapshot.looks && snapshot.looks.some(function (look) { return look.id === choice.val() && look.selectable === true; });
            strengthGroup.toggle(!!(snapshot && snapshot.strength_supported === true));
            choice.prop('disabled', busy || !ready);
            strength.prop('disabled', busy || !ready || !allowed);
            apply.prop('disabled', busy || !ready || !allowed);
            stock.prop('disabled', busy || !ready);
            refresh.prop('disabled', busy);
            sync.prop('disabled', busy || !canSync);
        }
        function render(result) {
            snapshot = result;
            var looks = Array.isArray(result.looks) ? result.looks : [], selection = result.selection || {}, hook = result.hook || {}, state = result.status || {};
            var supportsStrength = result.strength_supported === true;
            var selectedStrength = typeof selection.strength === 'number' && selection.strength % 1 === 0 && selection.strength >= 0 && selection.strength <= 100 ? selection.strength : 100;
            strengthGroup.toggle(supportsStrength);
            strength.val(selectedStrength);strengthValue.text(selectedStrength + '%');
            choice.empty().append($('<option value=""></option>').text(selection.stock ? '原厂' : '请选择风格'));
            looks.forEach(function (look) {
                choice.append($('<option></option>').val(look.id).text(look.label + (look.table_valid ? '' : '（表不可用）')).prop('disabled', look.selectable !== true));
            });
            if (selection.id) choice.val(selection.id);
            var label = looks.filter(function (look) { return look.id === selection.id; })[0];
            selected.text(selection.stock ? '当前选择：原厂' : selection.valid && label ? '当前选择：' + label.label + (supportsStrength ? ' · 强度 ' + selectedStrength + '%' : '') + ' · 代次 ' + selection.generation : '当前选择无效，请重新选择或恢复原厂');
            var stages = {
                'hook-unavailable': '原厂 LUT 桥未就绪或根目录不匹配',
                'invalid-selection': '当前选择无效',
                'staged-awaiting-stock-call': '已暂存，等待原厂 LUT 调用',
                'selected': '原厂已读取所选表，等待转发',
                'forwarded-to-stock-api': state.forwarded ? '已转发三路至原厂 LUT 接口' : '正在转发至原厂 LUT 接口（通道掩码 ' + state.forwarded_mask + '）',
                'stock-restore-awaiting-stock-call': '已选择原厂输入，等待原厂下一次 LUT 调用',
                'stock': '原厂已使用自身 LUT 输入',
                'missing-table': '原厂未找到所选表',
                'invalid-table': '原厂拒绝无效表'
            };
            firmware.text(stages[state.stage] || '原厂状态：' + (state.stage || '尚未取得'));
            ready = hook.ready === true;
            return ready;
        }
        function error(xhr) { return xhr.responseJSON && xhr.responseJSON.error || '无法读取机身 LUT 响应'; }
        function read() {
            if (busy) return;
            busy = true;ready = false;status.text('正在读取已部署风格…');update();
            $.ajax({url: '/api/v1/looks', dataType: 'json', cache: false, timeout: 14000}).done(function (result) {
                render(result);status.text(result.looks && result.looks.length ? '' : '当前目录没有已部署风格');
            }).fail(function (xhr) { snapshot = null;status.text(error(xhr) + '。请重新读取'); }).always(function () { busy = false;update(); });
        }
        function write(action, data) {
            if (busy || !ready) return;
            busy = true;ready = false;status.text('正在暂存选择…');update();
            $.ajax({url: '/api/v1/looks/' + action, method: 'POST', contentType: 'application/json; charset=utf-8', data: JSON.stringify(data), dataType: 'json', timeout: 14000})
                .done(function (result) { render(result);status.text(''); })
                .fail(function (xhr) { snapshot = null;status.text(error(xhr) + '。请重新读取后再修改'); })
                .always(function () { busy = false;update(); });
        }
        sync.on('click', function () {
            if (busy || !canSync) return;
            busy = true;ready = false;status.text('正在同步本地 LUT 库…');update();
            $.ajax({url: '/looks/sync', method: 'POST', contentType: 'application/json; charset=utf-8',
                data: '{}', dataType: 'json', timeout: 30000})
                .done(function (result) {
                    if (result.status !== 'synced') {
                        snapshot = null;busy = false;status.text('未确认同步结果，请重新读取');update();return;
                    }
                    $.ajax({url: '/api/v1/looks', dataType: 'json', cache: false, timeout: 14000})
                        .done(function (catalog) {
                            render(catalog);
                            status.text('已同步 ' + result.count + ' 项。若更新了当前风格，请重新暂存所选风格，等待原厂重载。');
                        })
                        .fail(function (xhr) { snapshot = null;status.text('文件已同步，但读取目录失败：' + error(xhr) + '。请重新读取'); })
                        .always(function () { busy = false;update(); });
                })
                .fail(function (xhr) { snapshot = null;busy = false;status.text('同步未完成：' + error(xhr) + '。请重新读取后重试');update(); });
        });
        apply.on('click', function () {
            if (apply.prop('disabled')) return;
            var data = {id: choice.val()};
            if (snapshot && snapshot.strength_supported === true) data.strength = Number(strength.val());
            write('select', data);
        });
        stock.on('click', function () { if (!stock.prop('disabled')) write('stock', {}); });
        choice.on('change', update);
        strength.on('input change', function () { strengthValue.text(strength.val() + '%'); });
        refresh.on('click', read);
        entry.on('click', function (event) { event.preventDefault();modal.modal('show'); });
        modal.on('show.bs.modal', function () {
            read();
            // Only the local development viewer owns the host LUT library.
            // Native camera deployments keep using their deployed catalogue.
            $.ajax({url: '/simulator/info', dataType: 'json', cache: false, timeout: 3000})
                .done(function (info) {
                    canSync = info.simulator === true && info.capabilities && info.capabilities.look_library_sync === true;
                    sync.toggle(!!canSync);update();
                })
                .fail(function () { canSync = false;sync.hide();update(); });
        });update();
    };
})();
