/* Native gallery adaptation. Listing and downloads retain the file protocol;
 * pairing changes only the displayed item and its explicit format choices. */
(function () {
    'use strict';
    Gallery.MEDIA_EXTS['.srw'] = 'raw';
    Gallery.MEDIA_EXTS['.dng'] = 'raw';
    Gallery.mediaUrl = function (relative) {
        return window.location.origin + '/DCIM/' + relative.split('/').map(encodeURIComponent).join('/');
    };
    Gallery.dirRelPath = function (url) {
        try {
            var path = new URL(url, window.location.href).pathname;
            if (path === '/DCIM') return '';
            if (path.indexOf('/DCIM/') !== 0) return null;
            return path.slice(6).split('/').map(decodeURIComponent).join('/').replace(/\/+$/, '');
        } catch (error) { return null; }
    };
    Gallery.prototype.fetchDirJson = function (url) {
        var relative = Gallery.dirRelPath(url);
        if (relative === null) return Promise.reject(new Error('not a media directory'));
        return new Promise(function (resolve, reject) {
            $.ajax({url: '/cgi-bin/dirlist?p=' + encodeURIComponent(relative), dataType: 'json', cache: false, timeout: 15000})
                .done(function (result) {
                    if (!result || result.ok !== true) { reject(new Error('directory unavailable'));return; }
                    var prefix = relative ? relative + '/' : '';
                    resolve({
                        dirs: (result.dirs || []).map(function (name) { return Gallery.mediaUrl(prefix + name) + '/'; }),
                        files: (result.files || []).map(function (name) {
                            var match = /\.[^.]+$/.exec(name), ext = match ? match[0].toLowerCase() : '';
                            return {name: name, ext: ext, url: Gallery.mediaUrl(prefix + name)};
                        }).filter(function (file) { return !!Gallery.MEDIA_EXTS[file.ext]; })
                    });
                }).fail(function () { reject(new Error('directory request failed')); });
        });
    };
    Gallery.prototype.fetchRootTree = function (rootUrl) {
        var self = this;
        self.fetchDir(rootUrl).then(function (result) {
            $('#gallery .gallery-loading').remove();
            self.groups = result.dirs.map(function (url) {
                var relative = Gallery.dirRelPath(url);
                return {name: relative.split('/').pop(), url: url, loaded: false, loading: false, items: []};
            }).sort(function (a, b) { return a.name < b.name ? 1 : a.name > b.name ? -1 : 0; });
            if (!self.groups.length) self.groups.push({name: 'DCIM', url: rootUrl, loaded: false, loading: false, items: []});
            self.renderDirSections();self.finishTreeLoad();
            for (var i = 0; i < Math.min(Gallery.DEFAULT_EXPAND, self.groups.length); i++) self.toggleDir(self.groups[i].name, true);
        }, function () {
            self.rootFailed = true;
            $('#gallery .gallery-loading').remove();
            $('#gallery .gallery-count').text('读取失败');
            $('#gallery').append($('<div class="gallery-error"></div>').text('无法读取相机相册目录，请刷新重试。'));
        });
    };
    Gallery.prototype.startPrewarmPoll = function () {};
    Gallery.groupFiles = function (files, directory) {
        var stems = Object.create(null), used = Object.create(null), groups = [];
        files.forEach(function (file) {
            if (!/^\.(jpe?g|srw)$/i.test(file.ext)) return;
            var stem = file.name.replace(/\.[^.]+$/, '').toLowerCase();
            if (!stems[stem]) stems[stem] = {jpeg: [], raw: []};
            stems[stem][file.ext === '.srw' ? 'raw' : 'jpeg'].push(file);
        });
        files.forEach(function (file) {
            if (used[file.url]) return;
            var stem = file.name.replace(/\.[^.]+$/, '').toLowerCase(), pair = stems[stem];
            var variants = null, primary = file;
            if (/^\.(jpe?g|srw)$/i.test(file.ext) && pair && pair.jpeg.length === 1 && pair.raw.length === 1) {
                primary = pair.jpeg[0];variants = {jpeg: pair.jpeg[0], raw: pair.raw[0]};
                used[pair.jpeg[0].url] = used[pair.raw[0].url] = true;
            } else if (/^\.jpe?g$/i.test(file.ext)) variants = {jpeg: file};
            else if (Gallery.MEDIA_EXTS[file.ext] === 'raw') variants = {raw: file};
            if (variants) Object.keys(variants).forEach(function (format) { variants[format].dir = directory; });
            groups.push({primary: primary, variants: variants});used[file.url] = true;
        });
        return groups;
    };
    var buildDirGrid = Gallery.prototype.buildDirGrid;
    Gallery.prototype.buildDirGrid = function (group, files) {
        var metadata = Object.create(null), grouped = Gallery.groupFiles(files, group.name);
        grouped.forEach(function (item) { metadata[item.primary.url] = item.variants; });
        this._nxBuildingVariants = metadata;
        try { buildDirGrid.call(this, group, grouped.map(function (item) { return item.primary; })); }
        finally { this._nxBuildingVariants = null; }
    };
    Gallery.prototype.previewUrls = function (file, size) {
        var sources = file.variants ? [file.variants.raw, file.variants.jpeg].filter(Boolean) : [file];
        return sources.map(function (source) {
            var relative = Gallery.dirRelPath(source.url);
            return relative === null ? '' : '/api/v1/gallery/preview?p=' + encodeURIComponent(relative) + '&size=' + (size === 'full' ? 'full' : 'grid');
        }).filter(Boolean);
    };
    Gallery.prototype.previewUrl = function (file, size) { return this.previewUrls(file, size)[0] || ''; };
    Gallery.prototype.thumbUrl = function (file, size) { return file.type === 'video' ? file.url : this.previewUrl(file, size); };
    Gallery.prototype.imageFallback = function (file) {
        if (file.variants) return file.variants.jpeg ? file.variants.jpeg.url : null;
        return file.type === 'image' ? file.url : null;
    };
    var buildItem = Gallery.prototype.buildItem;
    Gallery.prototype.buildItem = function (item) {
        var file = item.file;
        if (this._nxBuildingVariants) file.variants = this._nxBuildingVariants[file.url];
        var tile = buildItem.apply(this, arguments), image = tile.find('img');
        if (file.type !== 'video') {
            var fallback = this.imageFallback(file), candidates = this.previewUrls(file, 'grid');
            var missing = $('<span class="nx-gallery-no-preview"></span>').text(file.type === 'raw' ? 'RAW · 无可用预览' : '预览不可用').hide();
            tile.append(missing);
            image.off('error').on('error.nxgallery', function () {
                var attempt = candidates.indexOf(image.attr('src'));
                if (attempt >= 0 && attempt + 1 < candidates.length) { image.attr('src', candidates[attempt + 1]);return; }
                if (fallback && image.attr('src') !== fallback) { image.attr('src', fallback);return; }
                image.removeAttr('src').addClass('gallery-img-fallback');missing.show();
            }).on('load.nxgallery', function () { image.removeClass('gallery-img-fallback');missing.hide(); });
        }
        var paired = file.variants && file.variants.jpeg && file.variants.raw;
        var badge = paired ? 'RAW + JPEG' : file.type === 'raw' ? 'RAW' : '';
        if (badge) tile.append($('<span class="nx-gallery-format-badge"></span>').text(badge));
        tile.attr('role', 'button').attr('tabindex', '0').attr('aria-label', file.name + (badge ? '，' + badge : ''));
        tile.on('keydown.nxgallery', function (event) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault();tile.trigger('click'); } });
        return tile;
    };
    Gallery.prototype.filesForFormat = function (file, format) {
        if (!file.variants) return [file];
        var variants = file.variants, names = Object.keys(variants);
        if (!format) return names.length === 1 ? [variants[names[0]]] : [];
        if (format === 'both') return names.map(function (name) { return variants[name]; });
        return variants[format] ? [variants[format]] : [];
    };
    Gallery.prototype.selectedDownloadFiles = function () {
        var self = this, seen = Object.create(null), files = [], format = self._nxDownloadFormat && self._nxDownloadFormat.val();
        Object.keys(self.selection).forEach(function (index) {
            if (!self.selection[index] || !self.media[index]) return;
            self.filesForFormat(self.media[index], format).forEach(function (file) { if (!seen[file.url]) { seen[file.url] = true;files.push(file); } });
        });
        return files;
    };
    Gallery.prototype.needsDownloadFormat = function () {
        var self = this;
        return !self._nxDownloadFormat.val() && Object.keys(self.selection).some(function (index) {
            var file = self.media[index];return self.selection[index] && file && file.variants && file.variants.jpeg && file.variants.raw;
        });
    };
    var buildToolbar = Gallery.prototype.buildToolbar;
    Gallery.prototype.buildToolbar = function () {
        var self = this, bar = buildToolbar.apply(this, arguments);
        // The native service exposes its JSON listing, not an HTML directory.
        bar.find('button').filter(function () { return $(this).text() === '原始目录视图'; }).remove();
        self._nxDownloadFormat = $('<select class="nx-gallery-format" aria-label="下载格式"><option value="">选择下载格式</option><option value="jpeg">JPEG</option><option value="raw">RAW</option><option value="both">JPEG + RAW</option></select>');
        self._nxDownloadFormat.on('change', function () { self.updateDownloadCount(); });
        self._downloadBtn.before(self._nxDownloadFormat.hide());
        return bar;
    };
    var updateDownloadCount = Gallery.prototype.updateDownloadCount;
    Gallery.prototype.updateDownloadCount = function () {
        updateDownloadCount.apply(this, arguments);
        if (!this._nxDownloadFormat) return;
        var count = this.selectedDownloadFiles().length, needsFormat = this.needsDownloadFormat();
        this._nxDownloadFormat.toggle(!!this.selectMode).prop('disabled', this.downloading);
        this._downloadBtn.text(needsFormat ? '请选择下载格式' : '下载选中（' + count + ' 个文件）').prop('disabled', this.downloading || needsFormat || !count);
        if (this.lightbox && this._nxLightboxFormat) {
            var current = this.media[this.current];
            this.lightbox.find('.gallery-lb-dl').prop('disabled', this.downloading || !current || !this.filesForFormat(current, this._nxLightboxFormat.val()).length);
            this._nxLightboxFormat.prop('disabled', this.downloading);
        }
    };
    Gallery.prototype.downloadFiles = function (files, done) {
        var self = this, index = 0, success = 0;
        if (!files.length || self.downloading) return;
        self.downloading = true;self.updateDownloadCount();
        function next() {
            if (index >= files.length) {
                self.downloading = false;self.updateDownloadCount();
                self._dlInfo.show().text('下载完成：成功 ' + success + '，失败 ' + (files.length - success));
                if (done) done();return;
            }
            self._dlInfo.show().text('正在下载 ' + (index + 1) + '/' + files.length + '：' + files[index].name);
            self.downloadOne(files[index], function (ok) { if (ok) success++;index++;next(); });
        }
        next();
    };
    Gallery.prototype.downloadSelected = function () {
        if (!this.needsDownloadFormat()) this.downloadFiles(this.selectedDownloadFiles());
    };
    var initLightbox = Gallery.prototype.initLightbox;
    Gallery.prototype.initLightbox = function () {
        initLightbox.apply(this, arguments);
        if (this._nxLightboxFormat) return;
        var self = this, lightbox = self.lightbox;
        self._nxLightboxFormat = $('<select class="nx-gallery-format" aria-label="当前照片下载格式"></select>');
        lightbox.find('.gallery-lb-dl').before(self._nxLightboxFormat).attr('aria-label', '下载当前照片').off('click').on('click', function () {
            var file = self.media[self.current];if (!file) return;
            self.downloadFiles(self.filesForFormat(file, self._nxLightboxFormat.val()));
        });
        self._nxLightboxFormat.on('change', function () {
            var file = self.media[self.current];
            lightbox.find('.gallery-lb-dl').prop('disabled', !file || !self.filesForFormat(file, $(this).val()).length || self.downloading);
        });
        lightbox.find('.gallery-lb-orig').off('click').on('click', function () {
            var file = self.media[self.current], fallback = file && self.imageFallback(file);
            if (fallback) { $(this).prop('disabled', true).text('加载中…');lightbox.find('.gallery-lb-image').data('nxFallback', true).attr('src', fallback); }
        });
    };
    Gallery.prototype.updateLightbox = function () {
        var self = this, file = self.media[self.current], lightbox = self.lightbox;
        if (!file || !lightbox) return;
        var image = lightbox.find('.gallery-lb-image'), video = lightbox.find('.gallery-lb-video'), spinner = lightbox.find('.gallery-lb-spinner'), original = lightbox.find('.gallery-lb-orig');
        image.off('.lb').hide().removeAttr('src');video.hide().removeAttr('src');
        lightbox.find('.nx-gallery-preview-note').remove();
        lightbox.find('.gallery-lb-counter').text((self.current + 1) + ' / ' + self.media.length + ' · ' + file.name);
        var paired = file.variants && file.variants.jpeg && file.variants.raw;
        self._nxLightboxFormat.empty();
        if (paired) self._nxLightboxFormat.append('<option value="">选择下载格式</option><option value="jpeg">JPEG</option><option value="raw">RAW</option><option value="both">JPEG + RAW</option>');
        else if (file.variants) self._nxLightboxFormat.append($('<option></option>').val(file.variants.raw ? 'raw' : 'jpeg').text(file.variants.raw ? 'RAW' : 'JPEG'));
        else self._nxLightboxFormat.append($('<option value=""></option>').text('原文件'));
        self._nxLightboxFormat.toggle(!!file.variants).trigger('change');
        original.hide();
        if (file.type === 'video') { spinner.hide();video.attr('src', file.url).show();return; }
        var fallback = self.imageFallback(file), candidates = self.previewUrls(file, 'full');
        spinner.show();image.data('nxFallback', false);
        image.on('load.lb', function () {
            spinner.hide();image.show();
            original.toggle(!!fallback && !image.data('nxFallback')).prop('disabled', false).text('查看原图');
        }).on('error.lb', function () {
            var attempt = candidates.indexOf(image.attr('src'));
            if (attempt >= 0 && attempt + 1 < candidates.length) { image.attr('src', candidates[attempt + 1]);return; }
            if (fallback && !image.data('nxFallback')) { image.data('nxFallback', true).attr('src', fallback);return; }
            spinner.hide();image.hide().removeAttr('src');original.hide();
            lightbox.find('.gallery-lb-stage').append($('<div class="nx-gallery-preview-note"></div>').text(file.type === 'raw' ? 'RAW 内没有可用预览，可选择下载原文件。' : '预览加载失败，可下载原文件。'));
        });
        image.attr('src', self.previewUrl(file, 'full'));
    };
})();
