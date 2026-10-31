/* Seven-dimensional session recipes; camera readback owns the applied state. */
(function () {
    'use strict';
    var initialize = Settings.init;
    Settings.init = function () {
        initialize.apply(this, arguments);
        if ($('#nxRecipeSettings').length) return;
        var modal = $('<div id="nxRecipeSettings" class="modal fade" tabindex="-1" role="dialog" aria-labelledby="nxRecipeTitle"></div>');
        var content = $('<div class="modal-dialog"><div class="modal-content"><div class="modal-header"><button type="button" class="close" data-dismiss="modal" aria-label="关闭">&times;</button><h4 id="nxRecipeTitle" class="modal-title">配方</h4></div><div class="modal-body"></div><div class="modal-footer"><span class="pull-left">仅当前相机会话</span><button type="button" class="btn btn-default" data-dismiss="modal">关闭</button></div></div></div>');
        var body = content.find('.modal-body');
        var choice = $('<select id="nxRecipeChoice" class="form-control"><option value="">正在读取…</option></select>');
        var values = $('<p class="nx-recipe-values"></p>');
        var apply = $('<button type="button" class="btn btn-primary">应用所选配方</button>');
        var stock = $('<button type="button" class="btn btn-default">恢复原厂</button>');
        var refresh = $('<button type="button" class="btn btn-default">读取／刷新状态</button>');
        var current = $('<p class="nx-recipe-current"></p>');
        var actual = $('<p class="nx-recipe-values"></p>');
        var progress = $('<div class="nx-recipe-stages" aria-live="polite"></div>');
        var status = $('<p role="status" aria-live="polite"></p>');
        body.append($('<div class="form-group"></div>').append($('<label for="nxRecipeChoice">已部署配方</label>'), choice, values),
            $('<div class="nx-recipe-actions"></div>').append(apply, stock),
            $('<p class="help-block">应用会替换当前七维设置与额外 RGB 增益。恢复原厂会清除这些会话设置。</p>'),
            current, actual, progress, status, refresh);
        modal.append(content).appendTo(document.body);
        var entry = $('<a href="#">配方…</a>');
        $('#settingsMenu').append($('<li></li>').append(entry));
        var catalogue = [], busy = false, ready = false, opened = false, timer = null, selectedOnce = false;
        var pollsLeft = 0, waiting = false, mutationError = '', catalogError = '', rereadOnIdle = false;
        var keys = ['red', 'green', 'blue', 'hue', 'saturation', 'sharpness', 'contrast'];
        var labels = ['R', 'G', 'B', 'H', 'SAT', 'SHARP', 'CONTRAST'];
        var stageKeys = ['rgb', 'chroma', 'sharpness', 'contrast'];
        var stageLabels = ['RGB', '色相／饱和度', '锐度', '对比度'];
        function describe(v) {
            return v ? keys.map(function (key, i) { return labels[i] + ' ' + v[key]; }).join(' · ') : '';
        }
        function selectedRecipe() { return catalogue.filter(function (recipe) { return recipe.id === choice.val(); })[0]; }
        function update() {
            var recipe = selectedRecipe();values.text(recipe ? describe(recipe.values) : '');
            choice.prop('disabled', busy || !catalogue.length);
            apply.prop('disabled', busy || !ready || !recipe);
            stock.prop('disabled', busy || !ready);
            refresh.prop('disabled', busy);
        }
        function errorText(code) {
            var errors = {'-110': '等待原厂输入', '-112': '当前拍摄模式不支持', '-111': '系数超出机身范围', '-114': '需恢复原厂色彩调整'};
            return errors[String(code)] || '机身错误 ' + code;
        }
        function render(result) {
            ready = true;
            var selection = result.selection || {};
            var initialStock = selection.stock && result.generation === 1 &&
                result.preview_applied_mask === 0 && result.capture_applied_mask === 0;
            var previewStages = result.stages && result.stages.preview;
            waiting = !initialStock && !result.superseded && !result.preview_applied && !!previewStages &&
                !stageKeys.some(function (key) { return previewStages[key] && previewStages[key].error; });
            if (result.superseded) current.text('当前配方已被独立控件修改，请重新应用所选配方或恢复原厂。');
            else if (selection.stock) current.text('当前：原厂');
            else if (selection.matches && selection.id) current.text('当前：' + selection.label + ' · 代次 ' + result.generation);
            else current.text('当前七维设置不匹配已部署配方');
            actual.text('机身回读：' + describe(result.values));
            if (!selectedOnce) {
                if (selection.matches && selection.id) choice.val(selection.id);
                selectedOnce = true;
            }
            progress.empty();
            ['preview', 'capture'].forEach(function (kind) {
                var stages = result.stages && result.stages[kind];
                var row = $('<div class="nx-recipe-stage"></div>');
                row.append($('<strong></strong>').text(kind === 'preview' ? '预览' : '拍摄'));
                stageKeys.forEach(function (key, i) {
                    var state = stages && stages[key], text;
                    if (initialStock) text = '原厂';
                    else if (!state) text = '尚未读取';
                    else if (state.error) text = errorText(state.error);
                    else if (!state.available) text = kind === 'capture' ? '待拍摄更新' : '等待预览调用';
                    else if (result.superseded) text = '已被独立设置替换';
                    else if (state.applied) text = key === 'contrast' ? '曲线参数已转交' : '已处理';
                    else text = '等待更新';
                    row.append($('<span></span>').text(stageLabels[i] + '：' + text));
                });
                progress.append(row);
            });
        }
        function schedule() {
            if (timer) clearTimeout(timer);
            timer = opened && waiting && pollsLeft > 0 ? setTimeout(function () { read(true); }, 1500) : null;
        }
        function finishRequest(readState) {
            busy = false;update();
            if (!opened) return;
            if (rereadOnIdle) { rereadOnIdle = false;load();return; }
            if (readState) read(false);else schedule();
        }
        function failure(xhr) {
            var result = xhr.responseJSON || {};
            if (result.readback) render(result.readback);
            else { ready = false;current.text('当前机身状态尚未确认');progress.empty();actual.text(''); }
            var text = result.error || '未取得机身响应';
            if (result.mutation_may_have_been_applied) text += '；请求可能已执行，请读取状态确认';
            if (result.operation === 'set' || result.operation === 'reset') mutationError = text;
            status.text(text);
        }
        function read(automatic) {
            if (!opened) return;
            if (busy) { schedule();return; }
            busy = true;pollsLeft--;if (!automatic) status.text('正在读取机身配方状态…');update();
            $.ajax({url: '/api/v1/color/recipe', dataType: 'json', cache: false, timeout: 13000})
                .done(function (result) { render(result);status.text(catalogError || mutationError); })
                .fail(failure).always(function () { finishRequest(false); });
        }
        function write(data) {
            if (busy || !ready) return;
            if (timer) clearTimeout(timer);
            pollsLeft = 8;mutationError = '';busy = true;status.text('正在提交七维批次，等待机身回读…');update();
            $.ajax({url: '/api/v1/color/recipe', method: 'POST', contentType: 'application/json; charset=utf-8',
                data: JSON.stringify(data), dataType: 'json', timeout: 13000})
                .done(function (result) { render(result);status.text(''); })
                .fail(failure).always(function () { finishRequest(false); });
        }
        function load() {
            pollsLeft = 8;catalogError = '';busy = true;ready = false;status.text('正在读取配方目录…');update();
            $.ajax({url: '/api/v1/color/recipes', dataType: 'json', cache: false, timeout: 6000})
                .done(function (result) {
                    catalogue = Array.isArray(result.recipes) ? result.recipes : [];
                    choice.empty().append($('<option value=""></option>').text(catalogue.length ? '请选择配方' : '当前目录没有配方'));
                    catalogue.forEach(function (recipe) { choice.append($('<option></option>').val(recipe.id).text(recipe.label)); });
                    selectedOnce = false;
                })
                .fail(function (xhr) { catalogue = [];choice.empty().append($('<option value="">配方目录不可用</option>'));catalogError = xhr.responseJSON && xhr.responseJSON.error || '配方目录不可用';failure(xhr); })
                .always(function () { finishRequest(true); });
        }
        choice.on('change', function () { selectedOnce = true;update(); });
        apply.on('click', function () { if (!apply.prop('disabled')) write({id: choice.val()}); });
        stock.on('click', function () { if (!stock.prop('disabled')) write({reset: true}); });
        refresh.on('click', function () { mutationError = '';pollsLeft = 8;if (!catalogue.length) load();else read(false); });
        entry.on('click', function (event) { event.preventDefault();modal.modal('show'); });
        modal.on('show.bs.modal', function () { opened = true;if (busy) rereadOnIdle = true;else load(); });
        modal.on('hide.bs.modal', function () { opened = false;rereadOnIdle = false;if (timer) clearTimeout(timer);timer = null; });
        update();
    };
})();
