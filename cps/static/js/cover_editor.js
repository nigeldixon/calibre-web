/* global Cropper, $ */

(function () {
    "use strict";

    function getCsrfToken() {
        return $('input[name="csrf_token"]').first().val() || '';
    }

    function initCoverEditor(bookId) {
        var $modal = $('#coverEditorModal');
        if (!$modal.length) return;

        var $image = $('#cover-editor-image');
        var $brightness = $('#cover-brightness');
        var $contrast = $('#cover-contrast');
        var $saturation = $('#cover-saturation');
        var $valB = $('#cover-brightness-val');
        var $valC = $('#cover-contrast-val');
        var $valS = $('#cover-saturation-val');
        var $zoom = $('#cover-zoom');
        var $zoomVal = $('#cover-zoom-val');
        var $status = $('#cover-editor-status');
        var $history = $('#cover-history-list');
        var cropper = null;
        var originalSrc = $modal.data('cover-src');
        var syncingZoom = false;

        function setStatus(msg, isError) {
            $status.text(msg || '');
            $status.toggleClass('text-danger', !!isError);
            $status.toggleClass('text-success', !isError && !!msg);
        }

        function filterString() {
            var b = ($brightness.val() || 100);
            var c = ($contrast.val() || 100);
            var s = ($saturation.val() || 100);
            return 'brightness(' + b + '%) contrast(' + c + '%) saturate(' + s + '%)';
        }

        function updateFilter() {
            $valB.text($brightness.val() + '%');
            $valC.text($contrast.val() + '%');
            $valS.text($saturation.val() + '%');
            // apply to cropper view/canvas area for preview
            var f = filterString();
            $modal.find('.cropper-container img, .cropper-canvas img, .cropper-view-box img').css('filter', f);
            $image.css('filter', f);
        }

        function resetAdjustments() {
            $brightness.val(100);
            $contrast.val(100);
            $saturation.val(100);
            updateFilter();
        }

        function currentZoomRatio() {
            if (!cropper) return 1;
            var data = cropper.getImageData();
            if (!data || !data.naturalWidth) return 1;
            return data.width / data.naturalWidth;
        }

        function setZoomUI(ratio) {
            if (!$zoom.length) return;
            var pct = Math.round(ratio * 100);
            var min = parseInt($zoom.attr('min'), 10) || 10;
            var max = parseInt($zoom.attr('max'), 10) || 400;
            if (pct < min) pct = min;
            if (pct > max) pct = max;
            $zoom.val(pct);
            $zoomVal.text(Math.round(ratio * 100) + '%');
        }

        function syncZoomFromCropper() {
            if (!cropper) return;
            syncingZoom = true;
            setZoomUI(currentZoomRatio());
            syncingZoom = false;
        }

        function destroyCropper() {
            if (cropper) {
                cropper.destroy();
                cropper = null;
            }
        }

        function setDragMode(mode) {
            if (!cropper) return;
            cropper.setDragMode(mode);
            $('#cover-drag-crop').toggleClass('active', mode === 'crop');
            $('#cover-drag-move').toggleClass('active', mode === 'move');
        }

        function initCropper() {
            destroyCropper();
            $image.css('filter', 'none');
            cropper = new Cropper($image[0], {
                viewMode: 1,
                autoCropArea: 1,
                responsive: true,
                background: true,
                checkCrossOrigin: false,
                zoomOnWheel: true,
                wheelZoomRatio: 0.05,
                zoomOnTouch: true,
                dragMode: 'crop',
                ready: function () {
                    syncZoomFromCropper();
                },
                zoom: function () {
                    if (syncingZoom) return;
                    syncZoomFromCropper();
                }
            });
            // reapply filter after cropper builds DOM
            setTimeout(function () {
                updateFilter();
                syncZoomFromCropper();
                setDragMode('crop');
            }, 0);
        }

        function loadHistory() {
            $history.html('<li class="text-muted">' + ($history.data('loading') || 'Loading...') + '</li>');
            $.getJSON('/ajax/coverhistory/' + bookId)
                .done(function (data) {
                    if (!data || !data.success) {
                        $history.html('<li class="text-danger">Failed to load history</li>');
                        return;
                    }
                    var versions = data.versions || [];
                    if (!versions.length) {
                        $history.html('<li class="text-muted">' + ($history.data('empty') || 'No saved versions') + '</li>');
                        return;
                    }
                    var html = '';
                    versions.forEach(function (v) {
                        html += '<li class="cover-history-item" data-version="' + $('<div>').text(v.name).html() + '">' +
                            '<span class="cover-history-name">' + $('<div>').text(v.name).html() + '</span>' +
                            ' <button type="button" class="btn btn-xs btn-default cover-history-restore">' + ($history.data('restore') || 'Restore') + '</button>' +
                            ' <button type="button" class="btn btn-xs btn-danger cover-history-delete">' + ($history.data('delete') || 'Delete') + '</button>' +
                            '</li>';
                    });
                    $history.html(html);
                })
                .fail(function () {
                    $history.html('<li class="text-danger">Failed to load history</li>');
                });
        }

        function refreshCoverPreview(coverUrl) {
            if (!coverUrl) return;
            $('#detailcover').attr('src', coverUrl);
            // also update modal source for next open if desired
            $modal.data('cover-src', coverUrl);
            originalSrc = coverUrl;
        }

        $modal.on('shown.bs.modal', function () {
            setStatus('');
            resetAdjustments();
            $image.off('load.covereditor');
            var src = $modal.data('cover-src') || originalSrc;
            if (src) {
                $image.on('load.covereditor', function () {
                    initCropper();
                });
                if ($image.attr('src') === src) {
                    // may be cached
                    if ($image[0].complete) {
                        initCropper();
                    }
                } else {
                    $image.attr('src', src);
                }
            }
            loadHistory();
        });

        $modal.on('hidden.bs.modal', function () {
            destroyCropper();
            setStatus('');
            setZoomUI(1);
            $('#cover-drag-crop').addClass('active');
            $('#cover-drag-move').removeClass('active');
        });

        $brightness.add($contrast).add($saturation).on('input change', updateFilter);

        $('#cover-reset-adjustments').on('click', function () {
            resetAdjustments();
        });

        $zoom.on('input change', function () {
            if (!cropper || syncingZoom) return;
            var target = (parseInt($(this).val(), 10) || 100) / 100;
            syncingZoom = true;
            try {
                cropper.zoomTo(target);
            } catch (e) {}
            $zoomVal.text(Math.round(target * 100) + '%');
            syncingZoom = false;
            syncZoomFromCropper();
        });

        $('#cover-zoom-in').on('click', function () {
            if (!cropper) return;
            cropper.zoom(0.1);
        });

        $('#cover-zoom-out').on('click', function () {
            if (!cropper) return;
            cropper.zoom(-0.1);
        });

        $('#cover-reset-crop').on('click', function () {
            if (!cropper) return;
            cropper.reset();
            syncZoomFromCropper();
            setDragMode('crop');
            setTimeout(updateFilter, 0);
        });

        $('#cover-drag-crop').on('click', function () {
            setDragMode('crop');
        });

        $('#cover-drag-move').on('click', function () {
            setDragMode('move');
        });

        $('#cover-save-btn').on('click', function () {
            if (!cropper) return;
            setStatus($modal.data('saving') || 'Saving...', false);
            var canvas = cropper.getCroppedCanvas({ maxWidth: 4096, maxHeight: 4096 });
            if (!canvas) {
                setStatus('Could not create image', true);
                return;
            }
            try {
                var out = document.createElement('canvas');
                out.width = canvas.width;
                out.height = canvas.height;
                var ctx = out.getContext('2d');
                if (ctx && typeof ctx.filter !== 'undefined') {
                    ctx.filter = filterString();
                }
                ctx.drawImage(canvas, 0, 0);
                // if ctx.filter not supported, fallback: basic pixel adjust (simple)
                if (!ctx || typeof ctx.filter === 'undefined') {
                    applyPixelFilters(out, ctx, parseInt($brightness.val(),10), parseInt($contrast.val(),10), parseInt($saturation.val(),10));
                }
                out.toBlob(function (blob) {
                    if (!blob) {
                        setStatus('Could not export image', true);
                        return;
                    }
                    var fd = new FormData();
                    fd.append('csrf_token', getCsrfToken());
                    fd.append('cover', blob, 'cover.jpg');
                    $.ajax({
                        url: '/ajax/editcover/' + bookId,
                        method: 'POST',
                        data: fd,
                        processData: false,
                        contentType: false,
                        headers: { 'X-CSRFToken': getCsrfToken() }
                    }).done(function (resp) {
                        if (resp && resp.success) {
                            setStatus($modal.data('saved') || 'Saved', false);
                            refreshCoverPreview(resp.cover_url);
                            loadHistory();
                        } else {
                            setStatus((resp && resp.error) || 'Save failed', true);
                        }
                    }).fail(function (xhr) {
                        var msg = 'Save failed';
                        if (xhr && xhr.responseJSON && xhr.responseJSON.error) msg = xhr.responseJSON.error;
                        setStatus(msg, true);
                    });
                }, 'image/jpeg', 0.92);
            } catch (e) {
                setStatus('Save failed', true);
            }
        });

        function applyPixelFilters(canvas, ctx, b, c, s) {
            // basic fallback: brightness/contrast/saturation via pixel manipulation
            try {
                var imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                var d = imgData.data;
                var bF = (b || 100) / 100;
                var cF = (c || 100) / 100;
                var sF = (s || 100) / 100;
                for (var i = 0; i < d.length; i += 4) {
                    var r = d[i], g = d[i+1], bl = d[i+2];
                    r *= bF; g *= bF; bl *= bF;
                    r = (r - 128) * cF + 128;
                    g = (g - 128) * cF + 128;
                    bl = (bl - 128) * cF + 128;
                    var gray = 0.2989*r + 0.587*g + 0.114*bl;
                    r = gray + (r - gray) * sF;
                    g = gray + (g - gray) * sF;
                    bl = gray + (bl - gray) * sF;
                    d[i] = Math.max(0, Math.min(255, r));
                    d[i+1] = Math.max(0, Math.min(255, g));
                    d[i+2] = Math.max(0, Math.min(255, bl));
                }
                ctx.putImageData(imgData, 0, 0);
            } catch (e) {}
        }

        $history.on('click', '.cover-history-delete', function () {
            var name = $(this).closest('.cover-history-item').data('version');
            if (!name) return;
            setStatus($modal.data('deleting') || 'Deleting...', false);
            $.ajax({
                url: '/ajax/coverhistory/' + bookId + '/' + encodeURIComponent(name),
                method: 'POST',
                data: { csrf_token: getCsrfToken(), action: 'delete' },
                headers: { 'X-CSRFToken': getCsrfToken() }
            }).done(function (resp) {
                if (resp && resp.success) {
                    setStatus('');
                    loadHistory();
                } else {
                    setStatus((resp && resp.error) || 'Delete failed', true);
                }
            }).fail(function (xhr) {
                var msg = 'Delete failed';
                if (xhr && xhr.responseJSON && xhr.responseJSON.error) msg = xhr.responseJSON.error;
                setStatus(msg, true);
            });
        });

        $history.on('click', '.cover-history-restore', function () {
            var name = $(this).closest('.cover-history-item').data('version');
            if (!name) return;
            setStatus($modal.data('restoring') || 'Restoring...', false);
            $.ajax({
                url: '/ajax/coverhistory/' + bookId + '/' + encodeURIComponent(name) + '/restore',
                method: 'POST',
                data: { csrf_token: getCsrfToken() },
                headers: { 'X-CSRFToken': getCsrfToken() }
            }).done(function (resp) {
                if (resp && resp.success) {
                    setStatus($modal.data('restored') || 'Restored', false);
                    refreshCoverPreview(resp.cover_url);
                    loadHistory();
                    // reload image in cropper
                    destroyCropper();
                    resetAdjustments();
                    var src = resp.cover_url;
                    $image.on('load.covereditor', function () { initCropper(); });
                    $image.attr('src', src);
                } else {
                    setStatus((resp && resp.error) || 'Restore failed', true);
                }
            }).fail(function (xhr) {
                var msg = 'Restore failed';
                if (xhr && xhr.responseJSON && xhr.responseJSON.error) msg = xhr.responseJSON.error;
                setStatus(msg, true);
            });
        });
    }

    $(function () {
        var $modal = $('#coverEditorModal');
        if (!$modal.length) return;
        var bookId = $modal.data('book-id');
        if (!bookId) return;
        initCoverEditor(bookId);
    });
})();
