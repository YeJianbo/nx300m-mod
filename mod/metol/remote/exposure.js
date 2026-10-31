/* Session settings through the camera's native file bridges. */
(function () {
    'use strict';
    var initialize = Settings.init;
    Settings.init = function () {
        initialize.apply(this, arguments);
        if ($('#nxExposureSettings').length) return;
        var modal = $('<div id="nxExposureSettings" class="modal fade" tabindex="-1" role="dialog" aria-labelledby="nxExposureTitle"></div>');
        var content = $('<div class="modal-dialog"><div class="modal-content"><div class="modal-header"><button type="button" class="close" data-dismiss="modal" aria-label="关闭">&times;</button><h4 class="modal-title" id="nxExposureTitle">机身设置</h4></div><div class="modal-body"></div><div class="modal-footer"><span class="pull-left">仅当前相机会话</span><button type="button" class="btn btn-default" data-dismiss="modal">关闭</button></div></div></div>');
        var body = content.find('.modal-body');
        var iso = $('<fieldset><legend>ISO</legend></fieldset>');
        function select(parent, name, label, options) {
            var group = $('<div class="form-group"></div>');
            group.append($('<label></label>').attr('for', 'nxExposure-' + name).text(label));
            var field = $('<select class="form-control"></select>').attr('id', 'nxExposure-' + name).attr('name', name);
            field.append($('<option value=""></option>').text('未读取'));
            options.forEach(function (item) { field.append($('<option></option>').val(item[0]).text(item[1])); });
            parent.append(group.append(field));return field;
        }
        var step = select(iso, 'iso_step', 'ISO 步长', [['one-third', '1/3 EV'], ['one-stop', '1 EV']]);
        var expansion = select(iso, 'iso_expansion', 'ISO 扩展', [['false', '关闭'], ['true', '开启']]);
        var maximum = select(iso, 'auto_iso_max', '自动 ISO 上限', [125,160,200,250,320,400,500,640,800,1000,1250,1600,2000,2500,3200,6400].map(function (value) { return [String(value), String(value)]; }));
        var saveIso = $('<button type="button" class="btn btn-primary">应用 ISO 设置</button>');
        iso.append(saveIso);
        var shutterGroup = $('<fieldset style="margin-top:20px"><legend>快门</legend></fieldset>');
        var fastest = select(shutterGroup, 'fastest', '最快快门上限', ['off','1/60','1/125','1/250','1/500','1/1000','1/2000','1/4000','1/6000','1/8000'].map(function (value) { return [value, value === 'off' ? '关闭限制' : value + ' 秒']; }));
        var saveShutter = $('<button type="button" class="btn btn-primary">应用快门上限</button>');
        shutterGroup.append(saveShutter);
        var dialGroup = $('<fieldset style="margin-top:20px"><legend>指令拨盘</legend></fieldset>');
        var dial = $('<input id="nxExposure-dial-lock" type="checkbox">');
        var saveDial = $('<button type="button" class="btn btn-primary">应用拨盘锁</button>');
        var dialState = $('<p style="margin-top:8px">未读取</p>');
        dialGroup.append($('<div class="checkbox"></div>').append($('<label for="nxExposure-dial-lock"></label>').append(dial, ' 锁定指令拨盘')), saveDial, dialState);
        var colorGroup = $('<fieldset style="margin-top:20px"><legend>RGB 通道增益</legend><p>百分比，100 为中性。</p></fieldset>');
        var rgb = {};
        [['red', 'R 红'], ['green', 'G 绿'], ['blue', 'B 蓝']].forEach(function (item) {
            var id = 'nxExposure-' + item[0];
            var field = $('<input type="number" min="0" max="200" step="1" inputmode="numeric" class="form-control" placeholder="未读取">').attr('id', id).attr('name', item[0]);
            colorGroup.append($('<div class="form-group"></div>').append($('<label></label>').attr('for', id).text(item[1]), field));
            rgb[item[0]] = field;
        });
        var saveColor = $('<button type="button" class="btn btn-primary">应用 RGB 增益</button>');
        var resetColor = $('<button type="button" class="btn btn-default" style="margin-left:8px">复位为 100</button>');
        var colorState = $('<p style="margin-top:8px"></p>');
        colorGroup.append(saveColor, resetColor, colorState);
        var chromaGroup = $('<fieldset style="margin-top:20px"><legend>NX500 色相／饱和度档位</legend><p>档位 0–20，10 为中性。</p></fieldset>');
        var chroma = {}, chromaFields = ['hue', 'saturation'];
        [['hue', '色相档位'], ['saturation', '饱和度档位']].forEach(function (item) {
            var id = 'nxExposure-chroma-' + item[0];
            var field = $('<input type="number" min="0" max="20" step="1" class="form-control" placeholder="未读取">').attr('id', id).attr('name', item[0]);
            chromaGroup.append($('<div class="form-group"></div>').append($('<label></label>').attr('for', id).text(item[1]), field));
            chroma[item[0]] = field;
        });
        var saveChroma = $('<button type="button" class="btn btn-primary">应用色相／饱和度档位</button>');
        var resetChroma = $('<button type="button" class="btn btn-default" style="margin-left:8px">复位为 10</button>');
        var chromaSupport = $('<p style="margin-top:8px"></p>');
        var chromaState = $('<p></p>');
        chromaGroup.append(saveChroma, resetChroma, chromaSupport, chromaState);
        var styles = {};
        function createStyle(kind, label, description) {
            var group = $('<fieldset style="margin-top:20px"></fieldset>').append($('<legend></legend>').text('NX500 ' + label + '档位'));
            group.append($('<p></p>').text(description));
            var id = 'nxExposure-source-' + kind;
            var field = $('<input type="number" min="0" max="20" step="1" inputmode="numeric" class="form-control" placeholder="未读取">').attr('id', id);
            var save = $('<button type="button" class="btn btn-primary"></button>').text('应用' + label + '档位');
            var reset = $('<button type="button" class="btn btn-default" style="margin-left:8px"></button>').text('恢复原厂' + label);
            var state = $('<p role="status" aria-live="polite" style="margin-top:8px"></p>');
            group.append($('<div class="form-group"></div>').append($('<label></label>').attr('for', id).text(label + '档位'), field), save, reset, state);
            styles[kind] = {kind: kind, label: label, group: group, field: field, save: save, reset: reset, state: state,
                ready: false, baseline: null, enabled: false, pending: false, pid: null, timer: null, polls: 0};
            return group;
        }
        var contrastGroup = createStyle('contrast', '对比度', '档位 0–20，10 为中性曲线。应用后替代原厂对比度；恢复原厂会关闭此覆盖。支持 2D P/A/S/M 标准／自定义1模式，预览采用 8→10→8 位量化适配。');
        var sharpnessGroup = createStyle('sharpness', '锐度', '档位 0–20，10 为中性因子。NX500 档位因子作用于 NX300M 原厂锐化；恢复原厂会关闭此覆盖。支持 2D P/A/S/M 标准／自定义1模式。');
        var pwGroup = $('<fieldset style="margin-top:20px"><legend>原厂画面参数</legend></fieldset>');
        var pw = {}, pwFields = ['saturation', 'sharpness', 'contrast', 'color_tone'];
        [['saturation', '饱和度', -4, 4], ['sharpness', '锐度', -4, 4], ['contrast', '对比度', -4, 4], ['color_tone', '色彩角度（-1 为关闭）', -1, 359]].forEach(function (item) {
            var id = 'nxExposure-' + item[0];
            var field = $('<input type="number" step="1" class="form-control" placeholder="未读取">').attr('id', id).attr('name', item[0]).attr('min', item[2]).attr('max', item[3]);
            pwGroup.append($('<div class="form-group"></div>').append($('<label></label>').attr('for', id).text(item[1]), field));
            pw[item[0]] = field;
        });
        var savePw = $('<button type="button" class="btn btn-primary">应用画面参数</button>');
        var pwState = $('<p style="margin-top:8px"></p>');
        pwGroup.append(savePw, pwState);
        var status = $('<p role="status" aria-live="polite" style="margin-top:16px"></p>');
        var refresh = $('<button type="button" class="btn btn-default">读取当前设置</button>');
        body.append(iso, shutterGroup, dialGroup, colorGroup, chromaGroup, contrastGroup, sharpnessGroup, pwGroup, status, refresh);modal.append(content).appendTo(document.body);
        var entry = $('<a href="#"></a>').text('机身设置…');
        $('#settingsMenu').append($('<li></li>').append(entry));
        var rereadOnIdle = false;
        var busy = false, isoReady = false, shutterReady = false, colorReady = false, pwReady = false, dialReady = false, dialBaseline = null, isoBaseline = {}, pwBaseline = {};
        var chromaReady = false, chromaBaseline = {}, chromaSupported = {}, chromaPid = null;
        var isoFields = [['iso_step', step], ['iso_expansion', expansion], ['auto_iso_max', maximum]];
        function isoChanges() {
            var changes = {};
            isoFields.forEach(function (item) {
                var name = item[0], value = item[1].val();
                if (value !== isoBaseline[name]) changes[name] = name === 'iso_expansion' ? value === 'true' : name === 'auto_iso_max' ? Number(value) : value;
            });
            return changes;
        }
        function rgbValues() {
            var values = {}, valid = true;
            Object.keys(rgb).forEach(function (name) {
                var raw = rgb[name].val(), value = Number(raw);
                if (!/^\d+$/.test(raw) || value < 0 || value > 200 || Math.floor(value) !== value) valid = false;
                values[name] = value;
            });
            return valid ? values : null;
        }
        function pwChanges() {
            var changes = {}, valid = true;
            pwFields.forEach(function (name) {
                var raw = pw[name].val(), value = Number(raw), tone = name === 'color_tone';
                if (!/^-?\d+$/.test(raw) || Math.floor(value) !== value || value < (tone ? -1 : -4) || value > (tone ? 359 : 4)) valid = false;
                if (value !== pwBaseline[name]) changes[name] = value;
            });
            return valid ? changes : null;
        }
        function chromaChanges() {
            var changes = {}, valid = true;
            chromaFields.forEach(function (name) {
                var raw = chroma[name].val(), value = Number(raw);
                if (!/^\d+$/.test(raw) || Math.floor(value) !== value || value < 0 || value > 20) valid = false;
                if (chromaSupported[name] && value !== chromaBaseline[name]) changes[name] = value;
            });
            return valid ? changes : null;
        }
        function styleValue(style) {
            var raw = style.field.val(), value = Number(raw);
            return /^\d+$/.test(raw) && Math.floor(value) === value && value >= 0 && value <= 20 ? value : null;
        }
        function stopStylePoll(style) { if (style.timer !== null) clearTimeout(style.timer);style.timer = null; }
        function stopStylePolls() { Object.keys(styles).forEach(function (kind) { stopStylePoll(styles[kind]); }); }
        function update() {
            iso.find('select').prop('disabled', busy || !isoReady);
            fastest.prop('disabled', busy || !shutterReady);
            colorGroup.find('input').prop('disabled', busy || !colorReady);
            pwGroup.find('input').prop('disabled', busy || !pwReady);
            dial.prop('disabled', busy || !dialReady);
            chromaFields.forEach(function (name) { chroma[name].prop('disabled', busy || !chromaReady || !chromaSupported[name]); });
            saveIso.prop('disabled', busy || !isoReady || !step.val() || !expansion.val() || !maximum.val() || !Object.keys(isoChanges()).length);
            saveShutter.prop('disabled', busy || !shutterReady || !fastest.val());
            saveColor.prop('disabled', busy || !colorReady || !rgbValues());
            resetColor.prop('disabled', busy || !colorReady);
            var changes = pwChanges();
            savePw.prop('disabled', busy || !pwReady || !changes || !Object.keys(changes).length);
            saveDial.prop('disabled', busy || !dialReady || dial.prop('checked') === dialBaseline);
            var chromaDelta = chromaChanges();
            saveChroma.prop('disabled', busy || !chromaReady || !chromaDelta || !Object.keys(chromaDelta).length);
            resetChroma.prop('disabled', busy || !chromaReady || !(chromaSupported.hue || chromaSupported.saturation));
            Object.keys(styles).forEach(function (kind) {
                var style = styles[kind], value = styleValue(style);
                style.field.prop('disabled', busy || !style.ready);
                style.save.prop('disabled', busy || !style.ready || value === null || style.enabled && value === style.baseline);
                style.reset.prop('disabled', busy || !style.ready || !style.enabled);
            });
            refresh.prop('disabled', busy);
        }
        function request(kind, data) {
            var path = kind === 'color' ? '/api/v1/color/rgb' : kind === 'chroma' ? '/api/v1/color/chroma' : styles[kind] ? '/api/v1/color/' + kind : kind === 'pw' ? '/api/v1/color/picture-wizard' : kind === 'dial' ? '/api/v1/controls/dial-lock' : '/api/v1/exposure/' + kind;
            return $.ajax({url: path, method: data ? 'POST' : 'GET',
                contentType: 'application/json; charset=utf-8', data: data ? JSON.stringify(data) : undefined,
                dataType: 'json', cache: false, timeout: 14000});
        }
        function renderIso(result) {
            var controls = result.controls || {};
            var maxControl = controls.auto_iso_max || {};
            var supported = maxControl.value_mapping_verified && Array.isArray(maxControl.supported_values) ? maxControl.supported_values : [125,160,200,250,320,400,500,640,800,1000,1250,1600,2000,2500,3200,6400];
            maximum.empty().append($('<option value=""></option>').text('未读取'));
            [125,160,200,250,320,400,500,640,800,1000,1250,1600,2000,2500,3200,6400,12800,25600].forEach(function (value) {
                if (supported.indexOf(value) !== -1) maximum.append($('<option></option>').val(String(value)).text(String(value)));
            });
            isoFields.forEach(function (item) {
                var control = controls[item[0]];
                if (control) {
                    var value = control.value_mapping_verified && control.value != null ? String(control.value) : '';
                    item[1].val(value);isoBaseline[item[0]] = value;
                }
            });
            isoReady = isoFields.every(function (item) { return !!isoBaseline[item[0]] && item[1].val() === isoBaseline[item[0]]; });
            return isoReady;
        }
        function renderShutter(result) {
            fastest.val(result.fastest_target);shutterReady = !!fastest.val();return shutterReady;
        }
        function renderColor(result) {
            var valid = ['red', 'green', 'blue'].every(function (name) {
                return typeof result[name] === 'number' && result[name] >= 0 && result[name] <= 200 && Math.floor(result[name]) === result[name];
            });
            Object.keys(rgb).forEach(function (name) { rgb[name].val(valid ? result[name] : ''); });
            function matrix(prefix, label) {
                if (!result[prefix + '_base_available']) return label + '：尚未收到原厂矩阵';
                if (result[prefix + '_setter_result']) return label + '：原厂返回 ' + result[prefix + '_setter_result'];
                return label + '：' + (result[prefix + '_modified'] ? '已应用增益' : result.active ? '未应用增益' : '原厂矩阵');
            }
            colorState.text(valid ? matrix('preview', '预览') + '；' + matrix('capture', '拍摄') : 'RGB 读回无效');
            colorReady = valid;return valid;
        }
        function renderPw(result) {
            var values = result.values || {};
            var valid = pwFields.every(function (name) {
                var value = values[name], tone = name === 'color_tone';
                return typeof value === 'number' && Math.floor(value) === value && value >= (tone ? -1 : -4) && value <= (tone ? 359 : 4);
            });
            pwBaseline = {};
            pwFields.forEach(function (name) { pw[name].val(valid ? values[name] : '');if (valid) pwBaseline[name] = values[name]; });
            pwState.text(valid ? (result.iq_matches ? 'IQ 读回与设置一致' : 'IQ 读回与设置不同') + '；' + (result.scalar_mode_enabled ? '当前模式启用三项画面参数' : '当前模式未启用饱和度、锐度和对比度') : '原厂画面参数读回无效');
            pwReady = valid;return valid;
        }
        function renderChroma(result) {
            var valid = chromaFields.every(function (name) {
                return typeof result[name] === 'number' && Math.floor(result[name]) === result[name] && result[name] >= 0 && result[name] <= 20 && typeof result['supported_' + name] === 'boolean';
            });
            chromaBaseline = {};chromaSupported = {};chromaPid = valid ? result.camera_pid : null;
            chromaFields.forEach(function (name) {
                chroma[name].val(valid ? result[name] : '');
                if (valid) { chromaBaseline[name] = result[name];chromaSupported[name] = result['supported_' + name]; }
            });
            chromaSupport.text(valid ? (chromaSupported.hue ? '色相可调' : '色相尚未接通') + '；' + (chromaSupported.saturation ? '饱和度支持 P/A/S/M 标准／自定义1模式，请先复位 Picture Wizard 色度调整' : '饱和度尚未接通，仅显示读回值') : 'NX500 档位读回无效');
            function path(prefix, label) {
                if (result[prefix + '_base_available'] !== 1) return label + '：尚未收到原厂参数';
                if (result[prefix + '_setter_result']) return label + '：原厂返回 ' + result[prefix + '_setter_result'];
                return label + '：' + (result[prefix + '_modified'] ? '已修改参数' : result.active ? '未应用修改' : '原厂参数');
            }
            chromaState.text(valid ? path('preview', '预览') + '；' + path('capture', '拍摄') : '');
            chromaReady = valid;return valid;
        }
        function styleApplied(result, prefix) {
            return result[prefix + '_generation'] === result.generation && result[prefix + '_error'] === 0 && result[prefix + '_modified'] === (result.enabled ? 1 : 0);
        }
        function styleMessage(style, result) {
            if (styleApplied(result, 'preview')) return result.enabled ? '预览已应用 NX500 ' + style.label + '档位' : '预览已恢复原厂' + style.label;
            if (result.preview_generation === result.generation && result.preview_error) return style.label + '已读回，预览未应用（错误 ' + result.preview_error + '）';
            return style.label + (result.enabled ? '覆盖已启用' : '覆盖已关闭') + '；预览待更新，正在读取状态…';
        }
        function renderStyle(style, result) {
            var integer = function (value) { return typeof value === 'number' && isFinite(value) && Math.floor(value) === value; };
            var valid = integer(result.level) && result.level >= 0 && result.level <= 20 && typeof result.enabled === 'boolean' && integer(result.generation) && result.generation > 0;
            ['capture', 'preview'].forEach(function (prefix) {
                valid = valid && integer(result[prefix + '_generation']) && result[prefix + '_generation'] >= 0 && integer(result[prefix + '_error']) && (result[prefix + '_modified'] === 0 || result[prefix + '_modified'] === 1);
            });
            style.ready = valid;style.pid = valid ? result.camera_pid : null;
            style.baseline = valid ? result.level : null;style.enabled = valid && result.enabled;
            style.pending = valid && !styleApplied(result, 'preview') && (result.preview_generation !== result.generation || result.preview_error === 0);
            style.field.val(valid ? result.level : '');
            function path(prefix, label) {
                if (result[prefix + '_generation'] !== result.generation) return label + '：待更新';
                if (result[prefix + '_error']) return label + '：未应用（错误 ' + result[prefix + '_error'] + '）';
                return label + '：' + (styleApplied(result, prefix) ? result.enabled ? '已应用' : '原厂' + style.label : '待更新');
            }
            style.state.text(valid ? (result.enabled ? '覆盖已启用（档位 ' + result.level + '）' : '覆盖已关闭') + '；' + path('preview', '预览') + '；' + path('capture', '拍摄') : 'NX500 ' + style.label + '读回无效');
            return valid;
        }
        function renderDial(result) {
            dialReady = typeof result.enabled === 'boolean';
            dialBaseline = dialReady ? result.enabled : null;
            dial.prop('checked', dialReady && result.enabled);
            dialState.text(dialReady ? result.enabled ? '指令拨盘已锁定' : '指令拨盘未锁定' : '拨盘锁读回无效');
            return dialReady;
        }
        function render(kind, result) { return kind === 'iso' ? renderIso(result) : kind === 'color' ? renderColor(result) : kind === 'chroma' ? renderChroma(result) : styles[kind] ? renderStyle(styles[kind], result) : kind === 'pw' ? renderPw(result) : kind === 'dial' ? renderDial(result) : renderShutter(result); }
        function error(xhr) { return xhr.responseJSON && xhr.responseJSON.error || '无法读取机身响应'; }
        function invalidate() { stopStylePolls();Object.keys(styles).forEach(function (kind) { var style = styles[kind];style.ready = false;style.pending = false;style.pid = null;style.baseline = null;style.enabled = false; });isoReady = false;shutterReady = false;colorReady = false;pwReady = false;dialReady = false;chromaReady = false;chromaBaseline = {};chromaSupported = {};chromaPid = null;dialBaseline = null;isoBaseline = {};pwBaseline = {}; }
        function pollStyle(style) {
            stopStylePoll(style);
            if (!style.ready || !style.pending || !modal.hasClass('in')) return;
            if (style.polls >= 12) { status.text(style.label + '请求已接受；预览仍待更新，可重新读取当前设置');return; }
            style.timer = setTimeout(function () {
                style.timer = null;
                if (!modal.hasClass('in')) return;
                if (busy) { pollStyle(style);return; }
                busy = true;style.polls++;update();
                var expectedPid = style.pid, draft = style.field.val(), dirty = styleValue(style) !== style.baseline;
                request(style.kind).done(function (result) {
                    if (result.camera_pid !== expectedPid || !renderStyle(style, result)) {
                        invalidate();status.text(style.label + '相机会话或读回已变化，请重新读取');return;
                    }
                    if (dirty) style.field.val(draft);
                    status.text(styleMessage(style, result));
                }).fail(function (xhr) { invalidate();status.text(error(xhr) + '。请重新读取' + style.label + '状态'); })
                    .always(function () { if (!finishRequest()) pollStyle(style); });
            }, 400);
        }
        function pollStyles() { Object.keys(styles).forEach(function (kind) { pollStyle(styles[kind]); }); }
        function finishRequest() {
            busy = false;update();
            if (rereadOnIdle && modal.hasClass('in')) { rereadOnIdle = false;readAll();return true; }
            return false;
        }
        function readAll() {
            if (busy) return;
            busy = true;invalidate();
            step.val('');expansion.val('');maximum.val('');fastest.val('');
            Object.keys(rgb).forEach(function (name) { rgb[name].val(''); });colorState.text('');
            pwFields.forEach(function (name) { pw[name].val(''); });pwState.text('');
            dial.prop('checked', false);dialState.text('未读取');
            chromaFields.forEach(function (name) { chroma[name].val(''); });chromaSupport.text('');chromaState.text('');
            Object.keys(styles).forEach(function (kind) { var style = styles[kind];style.field.val('');style.state.text('');style.polls = 0; });
            status.text('正在读取机身设置…');update();
            $.getJSON('/api/v1/camera/info').done(function (info) {
                var caps = info.capabilities || {}, errors = [], queue = [];
                colorGroup.toggle(caps.color_style === true);
                pwGroup.toggle(caps.picture_wizard === true);
                dialGroup.toggle(caps.dial_lock === true);
                chromaGroup.toggle(caps.chroma_style === true);
                Object.keys(styles).forEach(function (kind) { styles[kind].group.toggle(caps[kind + '_style'] === true); });
                if (caps.iso_controls === true) queue.push(['iso', 'ISO']);
                if (caps.shutter_limit === true) queue.push(['shutter', '快门']);
                if (caps.color_style === true) queue.push(['color', 'RGB']);
                if (caps.picture_wizard === true) queue.push(['pw', '画面参数']);
                if (caps.dial_lock === true) queue.push(['dial', '拨盘锁']);
                if (caps.chroma_style === true) queue.push(['chroma', 'NX500 色相／饱和度档位']);
                Object.keys(styles).forEach(function (kind) { if (caps[kind + '_style'] === true) queue.push([kind, 'NX500 ' + styles[kind].label + '档位']); });
                function next() {
                    if (!queue.length) {
                        if (finishRequest()) return;
                        status.text(errors.length ? errors.join('；') + '。请重新读取' : Object.keys(styles).some(function (kind) { return styles[kind].pending; }) ? '预览待更新，正在读取状态…' : !isoReady && !shutterReady && !colorReady && !pwReady && !dialReady && !chromaReady && !Object.keys(styles).some(function (kind) { return styles[kind].ready; }) ? '当前相机会话尚未启用机身控制' : '已读取当前机身设置');
                        pollStyles();return;
                    }
                    var item = queue.shift();
                    request(item[0]).done(function (result) { if (!render(item[0], result)) errors.push(item[1] + '：读回无效'); })
                        .fail(function (xhr) { errors.push(item[1] + '：' + error(xhr)); }).always(next);
                }
                next();
            }).fail(function (xhr) { if (!finishRequest()) status.text(error(xhr) + '。请重新读取'); });
        }
        function apply(kind, data) {
            if (busy) return;
            busy = true;status.text('正在应用机身设置…');update();
            request(kind, data).done(function (result) {
                if (render(kind, result)) status.text('设置已由机身读回');
                else { invalidate();status.text('设置读回无效，请重新读取'); }
            }).fail(function (xhr) { invalidate();status.text(error(xhr) + '。请重新读取后再修改'); }).always(finishRequest);
        }
        function applyChroma() {
            if (busy || !chromaReady) return;
            var changes = chromaChanges(), expectedPid = chromaPid;
            if (!changes || !Object.keys(changes).length) return;
            busy = true;status.text('正在读取最新 NX500 档位…');update();
            function finish() { finishRequest(); }
            function failure(xhr) { invalidate();status.text(error(xhr) + '。请重新读取后再修改'); }
            request('chroma').done(function (latest) {
                if (!renderChroma(latest) || latest.camera_pid !== expectedPid) {
                    invalidate();status.text('相机会话或档位读回已变化，请重新读取');finish();return;
                }
                var unsupported = Object.keys(changes).some(function (name) { return !chromaSupported[name]; });
                if (unsupported) { invalidate();status.text('所选档位当前不支持修改，请重新读取');finish();return; }
                var merged = {hue: latest.hue, saturation: latest.saturation};
                Object.keys(changes).forEach(function (name) { merged[name] = changes[name]; });
                status.text('正在应用 NX500 档位…');
                request('chroma', merged).done(function (result) {
                    if (renderChroma(result)) status.text('NX500 档位已读回');
                    else { invalidate();status.text('NX500 档位读回无效，请重新读取'); }
                }).fail(failure).always(finish);
            }).fail(function (xhr) { failure(xhr);finish(); });
        }
        function applyStyle(style, data) {
            if (busy || !style.ready) return;
            stopStylePolls();style.polls = 0;busy = true;
            var expectedPid = style.pid;
            status.text(data.reset ? '正在请求恢复原厂' + style.label + '…' : '正在设置 NX500 ' + style.label + '档位…');update();
            request(style.kind, data).done(function (result) {
                if (result.camera_pid === expectedPid && renderStyle(style, result)) status.text(styleMessage(style, result));
                else { invalidate();status.text(style.label + '相机会话或读回已变化，请重新读取'); }
            }).fail(function (xhr) { invalidate();status.text(error(xhr) + '。请重新读取后再修改'); })
                .always(function () { if (!finishRequest()) pollStyles(); });
        }
        saveIso.on('click', function () { if (!saveIso.prop('disabled')) apply('iso', isoChanges()); });
        saveShutter.on('click', function () { if (!saveShutter.prop('disabled')) apply('shutter', {fastest: fastest.val()}); });
        saveColor.on('click', function () { if (!saveColor.prop('disabled')) apply('color', rgbValues()); });
        resetColor.on('click', function () { if (!resetColor.prop('disabled')) apply('color', {reset: true}); });
        savePw.on('click', function () { if (!savePw.prop('disabled')) apply('pw', pwChanges()); });
        saveDial.on('click', function () { if (!saveDial.prop('disabled')) apply('dial', {enabled: dial.prop('checked')}); });
        saveChroma.on('click', function () { if (!saveChroma.prop('disabled')) applyChroma(); });
        resetChroma.on('click', function () { if (!resetChroma.prop('disabled')) apply('chroma', {reset: true}); });
        Object.keys(styles).forEach(function (kind) {
            var style = styles[kind];
            style.save.on('click', function () { if (!style.save.prop('disabled')) { var data = {};data[kind] = styleValue(style);applyStyle(style, data); } });
            style.reset.on('click', function () { if (!style.reset.prop('disabled')) applyStyle(style, {reset: true}); });
            style.field.on('input change', update);
        });
        iso.find('select').add(fastest).on('change', update);
        colorGroup.find('input').on('input change', update);
        pwGroup.find('input').on('input change', update);
        dial.on('change', update);
        chromaGroup.find('input').on('input change', update);
        refresh.on('click', readAll);
        entry.on('click', function (event) { event.preventDefault();modal.modal('show'); });
        modal.on('show.bs.modal', function () { if (busy) rereadOnIdle = true;else readAll(); });
        modal.on('hide.bs.modal', function () { rereadOnIdle = false;stopStylePolls(); });update();
    };
})();
