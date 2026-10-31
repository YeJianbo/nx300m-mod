#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_nv12.py —— NX500 liveview NV12 帧布局验证（纯本地，不需要相机）。

真相来源是 App 里的 Nv12Decoder.kt（真机验证过、能正确出图的实现）：

    fun expectedSize(width: Int, height: Int, low: Boolean): Int =
        if (low) width * height / 4 * 3 else width * height * 3 / 2

    expectedSize(720, 480, low = false) = 720*480*3/2 = 518400   // /api/v1/liveview/get
    expectedSize(720, 480, low = true ) = 720*480/4*3 = 259200   // /api/v1/liveview/get_low

    val yW = if (spec.low) w / 2 else w       // 低清 360，高清 720
    val yH = if (spec.low) h / 2 else h       // 低清 240，高清 480
    val uvW = w / 2                           // 360（以 U/V 对计）
    val uvOffset = yW * yH                    // 低清 86400，高清 345600
    val uvRowStride = uvW * 2                 // 720 字节/行（低清高清都一样！）
    // Y  索引：yIdx  = (y/2)*yW + (x/2)                      （低清）
    //          yIdx  = y*yW + x                               （高清）
    // UV 索引：uvIdx = uvOffset + (y/2)*uvRowStride + (x/2)*2 （两种布局同一套公式）
    // 取值：   u = body[uvIdx], v = body[uvIdx+1]

于是两种布局是（输出都是 720x480）：

    高清 low=false：Y 720x480 = 345600 字节；UV 240 行 x 720 字节 = 172800；合计 518400
                    uvOffset = 345600 = yW*yH
    低清 low=true ：Y 360x240 =  86400 字节；UV 240 行 x 720 字节 = 172800；合计 259200
                    uvOffset =  86400 = yW*yH = (720/2)*(480/2)

统一的不变量（这才是真的那个）：
    Y 平面字节数 + UV 平面字节数 == body.length
    UV 平面紧跟在 Y 平面之后，偏移 = Y 平面字节数
    UV 平面字节数 = uvRowStride * (h/2) = (w/2)*2*(h/2)

关键坑（真机上踩过，务必看这里）
--------------------------------
1) **低清 Y 平面也是半分辨率**：宽 360、高 240（不是 720x480，也不是 360x360）。
   旧 bug 把低清 Y 当成全分辨率（继续用 yIdx = y*720 + x），
   于是第 360 行开始 yIdx = 360*720 = 259200 == len(body)，
   真机直接抛 ArrayIndexOutOfBoundsException: length=259200; Index=259200。
   本文件有 test_low_y_half_resolution_regression() 专门复现这个崩溃。

2) **别把「半分辨率边长」当成「平面字节数」，也别把色度行跨度当成 Y 行跨度。**
   本文件最初就是按 “低清 Y 360x360 = 129600 + UV 180x180x2 = 64800 = 194400” 写的，
   结果和实测 Body 259200 差 64800，怎么都对不上（194400 != 259200 的矛盾就出在这里）。
   正确算法：
     * Y 平面的**宽和高都减半**：360x240 -> 86400 字节（不是 360x360）；
     * UV 平面是 (w/2) x (h/2) = 360x240 个 U/V **对**，每对 2 字节 -> 172800 字节，
       即 **UV 平面字节数 = 2 * Y 平面字节数**；不是「Y 的一半」，
       也不能再把 360 减半成 180（那正是 64800 的错误来源）；
     * UV 行的字节跨度 uvRowStride = (w/2)*2 = 720，而低清 Y 行的跨度只有 360 —— 两者不相等。
   这套布局把 259200 字节正好用满：最后一个输出像素 (719,479) 的 uvIdx+1 = 259199，
   Y 平面最后一个字节的 yIdx = 86399 = 86400-1。差一个字节就越界，所以边界检查很关键。

运行：python verify_nv12.py                    全部通过时退出码 0
      python verify_nv12.py --dump-ppm out/    把测试帧解码结果写成 PPM 便于肉眼看
      python verify_nv12.py --probe body.bin   拿真机抓到的 Body dump 校验布局假设
"""

from __future__ import annotations

import os
import sys

# --------------------------------------------------------------------------
# 布局（与 Nv12Decoder.kt 逐字对齐）
# --------------------------------------------------------------------------
OUT_W, OUT_H = 720, 480          # 两种接口的输出尺寸
HD_BODY = 518400                 # /api/v1/liveview/get
LOW_BODY = 259200                # /api/v1/liveview/get_low


def expected_size(width, height, low):
    """expectedSize(width, height, low)：低清 w*h/4*3，高清 w*h*3/2。"""
    if low:
        return width * height // 4 * 3
    return width * height * 3 // 2


class Layout:
    """一种 liveview 布局：Y 平面尺寸 + UV 平面尺寸/偏移 + 采样比例。"""

    __slots__ = ("low", "y_w", "y_h", "y_scale", "out_w", "out_h", "body")

    def __init__(self, low, y_w, y_h, y_scale, out_w, out_h, body):
        self.low = low              # True = /liveview/get_low
        self.y_w = y_w              # Y 平面宽 = yIdx 的行跨度（低清 360 / 高清 720）
        self.y_h = y_h              # Y 平面高 = 行数（低清 240 / 高清 480）
        self.y_scale = y_scale      # 输出 -> Y 平面：sy = y // y_scale, sx = x // y_scale
        self.out_w = out_w
        self.out_h = out_h
        self.body = body

    # ---- 派生量（全部照抄 Kotlin）----
    @property
    def y_plane(self):
        """Y 平面字节数 = yW*yH（低清 86400 / 高清 345600）。"""
        return self.y_w * self.y_h

    @property
    def uv_w(self):
        """UV 平面一行的 U/V 对数 = w/2（低清高清都是 360）。"""
        return self.out_w // 2

    @property
    def uv_row_stride(self):
        """UV 平面一行的字节数 = uvW*2（低清高清都是 720；注意 != 低清的 yW=360）。"""
        return self.uv_w * 2

    @property
    def uv_rows(self):
        """UV 平面行数 = h/2（低清高清都是 240）。"""
        return self.out_h // 2

    @property
    def uv_offset(self):
        """UV 平面起始偏移 = yW*yH（紧跟 Y 平面）。"""
        return self.y_plane

    @property
    def uv_bytes(self):
        return self.uv_row_stride * self.uv_rows

    @property
    def total_bytes(self):
        return self.y_plane + self.uv_bytes

    # ---- 索引（与 Kotlin 的 yIdx / uvIdx 完全一致）----
    def y_index(self, x, y):
        return (y // self.y_scale) * self.y_w + (x // self.y_scale)

    def uv_index(self, x, y):
        return self.uv_offset + (y // 2) * self.uv_row_stride + (x // 2) * 2

    def max_y_index(self):
        return self.y_index(self.out_w - 1, self.out_h - 1)

    def max_uv_index(self):
        return self.uv_index(self.out_w - 1, self.out_h - 1)

    def describe(self):
        return ("%s Y=%dx%d(%d 字节) UV=%d 行 x %d 字节(stride %d) UV字节=%d uvOffset=%d "
                "合计=%d 输出=%dx%d sy=y/%d sx=x/%d"
                % ("低清" if self.low else "高清", self.y_w, self.y_h, self.y_plane,
                   self.uv_rows, self.uv_row_stride, self.uv_row_stride, self.uv_bytes,
                   self.uv_offset, self.total_bytes, self.out_w, self.out_h,
                   self.y_scale, self.y_scale))


def layout_for(low):
    if low:
        return Layout(True, OUT_W // 2, OUT_H // 2, 2, OUT_W, OUT_H, LOW_BODY)
    return Layout(False, OUT_W, OUT_H, 1, OUT_W, OUT_H, HD_BODY)


def detect_layout(body_len):
    """
    按 Body 长度判定布局，返回 Layout；长度不认识就抛 ValueError。

    断言（两种布局都要成立）：
      * expectedSize(720, 480, low) == body_len
      * UV 偏移 = Y 平面字节数 = 86400（低清，(720/2)*(480/2)）
      * UV 偏移 + UV 平面字节数 == body_len
    """
    if body_len == HD_BODY:
        lay = layout_for(False)
    elif body_len == LOW_BODY:
        lay = layout_for(True)
    else:
        raise ValueError("未知的 liveview Body 长度 %d（高清应为 %d，低清应为 %d）"
                         % (body_len, HD_BODY, LOW_BODY))
    assert expected_size(OUT_W, OUT_H, lay.low) == body_len, (lay.describe(), body_len)
    assert lay.uv_offset == lay.y_plane == lay.y_w * lay.y_h, lay.describe()
    assert lay.uv_offset + lay.uv_bytes == body_len, lay.describe()
    return lay


# --------------------------------------------------------------------------
# NV12 -> RGB888
# --------------------------------------------------------------------------
def _clamp8(v):
    if v < 0:
        return 0
    if v > 255:
        return 255
    return v


def decode(yuv, low, strict=True):
    """
    把 NV12 Body 解成 720x480 的 RGB。

    返回 (w, h, pixels)，pixels 为 w*h*3 字节的 RGB888（bytes）。
    low=True 时用低清布局（Y 平面 360x240，输出仍是 720x480）。
    strict=True 时逐像素检查索引，越界抛 IndexError（带具体索引，便于对真机崩溃）。
    """
    lay = detect_layout(len(yuv))
    if lay.low != bool(low):
        raise ValueError("low=%s 与 Body 长度 %d 不符（该长度是%s）"
                         % (low, len(yuv), "低清" if lay.low else "高清"))
    return _decode_layout(yuv, lay, strict=strict)


def _decode_layout(yuv, lay, strict=True):
    """
    真正的解码实现。

    高清：sy=y、sx=x，yIdx = y*720 + x
    低清：sy=y/2、sx=x/2，yIdx = (y/2)*360 + (x/2)
    色度：uvIdx = uvOffset + (y/2)*uvRowStride + (x/2)*2（两种布局同一套公式）
          u = body[uvIdx]，v = body[uvIdx+1]
    """
    n = len(yuv)
    if strict and lay.total_bytes > n:
        # 布局和 Body 对不上：基本就是当年那个 bug（低清套了高清跨度）
        bad_y = bad_idx = None
        for y in range(lay.out_h):
            sy = y // lay.y_scale
            if sy >= lay.y_h or sy * lay.y_w >= n:
                bad_y, bad_idx = y, sy * lay.y_w
                break
        raise ValueError("布局 %s 需要 %d 字节，实际 %d 字节；按该布局第 %s 行 yIdx=%s 起就会越界"
                         "（低清 Y 平面只有 360x240，不是 720x480）"
                         % (lay.describe(), lay.total_bytes, n, bad_y, bad_idx))

    y_plane = lay.y_plane
    stride = lay.uv_row_stride
    out = bytearray(lay.out_w * lay.out_h * 3)

    # 每个输出列对应的 Y 列与 UV 列（循环外算一次）
    cols = []
    for x in range(lay.out_w):
        sx = x // lay.y_scale
        cols.append((sx, (x // 2) * 2))          # UV 列 = (x/2)*2，低清高清同一套

    o = 0
    for y in range(lay.out_h):
        sy = y // lay.y_scale                    # 低清：Y 行也减半
        y_row = sy * lay.y_w
        uv_row = y_plane + (y // 2) * stride     # 色度行 = 输出行/2（两种布局都一样）
        for sx, uvx in cols:
            y_idx = y_row + sx
            if strict and y_idx >= n:
                raise IndexError("Y 越界: yIdx=%d len=%d (%s)" % (y_idx, n, lay.describe()))
            uv_idx = uv_row + uvx
            if strict and uv_idx + 1 >= n:
                raise IndexError("UV 越界: uvIdx=%d len=%d (%s)" % (uv_idx, n, lay.describe()))
            c = yuv[y_idx] - 16
            d = yuv[uv_idx] - 128
            e = yuv[uv_idx + 1] - 128
            out[o] = _clamp8((298 * c + 409 * e + 128) >> 8)
            out[o + 1] = _clamp8((298 * c - 100 * d - 208 * e + 128) >> 8)
            out[o + 2] = _clamp8((298 * c + 516 * d + 128) >> 8)
            o += 3
    return lay.out_w, lay.out_h, bytes(out)


# --------------------------------------------------------------------------
# 测试用合成帧 / 取色工具
# --------------------------------------------------------------------------
def make_test_frame(low, fill=(16, 128, 128), patches=()):
    """
    造一个可控的 NV12 测试帧（长度正好等于真机 Body 长度）。

    fill    = (Y, U, V) 整帧底色（默认 Y=16,U=V=128 -> 纯黑）
    patches = [(x, y, w, h, Y, U, V), ...] 以 **输出像素坐标** 写色块，
              Y 平面与 UV 平面各自按上面的索引规则覆盖。
    """
    lay = layout_for(low)
    buf = bytearray(lay.body)
    fy, fu, fv = fill
    buf[:lay.y_plane] = bytes([fy]) * lay.y_plane                     # Y 平面
    buf[lay.uv_offset:lay.uv_offset + lay.uv_bytes] = \
        bytes([fu, fv]) * (lay.uv_bytes // 2)                         # UV 平面对

    for (px, py, pw, ph, py8, pu8, pv8) in patches:
        x0, y0 = max(0, min(px, lay.out_w)), max(0, min(py, lay.out_h))
        x1, y1 = max(x0, min(px + pw, lay.out_w)), max(y0, min(py + ph, lay.out_h))
        # Y 平面覆盖：低清时坐标减半
        k = lay.y_scale
        sy0, sy1 = y0 // k, min(lay.y_h, -(-y1 // k))
        sx0, sx1 = x0 // k, min(lay.y_w, -(-x1 // k))
        for sy in range(sy0, sy1):
            buf[sy * lay.y_w + sx0: sy * lay.y_w + sx1] = bytes([py8]) * (sx1 - sx0)
        # UV 平面覆盖：色度块 = 输出坐标/2（低清高清同一套）
        uy0, uy1 = y0 // 2, min(lay.uv_rows, -(-y1 // 2))
        ux0, ux1 = x0 // 2, min(lay.uv_w, -(-x1 // 2))
        if ux1 > ux0:
            uv_col = bytes([pu8, pv8]) * (ux1 - ux0)
            for uy in range(uy0, uy1):
                off = lay.uv_offset + uy * lay.uv_row_stride + ux0 * 2
                buf[off:off + len(uv_col)] = uv_col
    return bytes(buf)


def pixel(pixels, x, y, w=OUT_W):
    """取输出图像 (x, y) 处的 RGB 三元组。"""
    i = (y * w + x) * 3
    return (pixels[i], pixels[i + 1], pixels[i + 2])


def rgb_reference(y8, u8, v8):
    """
    独立实现的浮点参考（BT.601 limited range），用来交叉验证整数公式：
        R = 1.164*C + 1.596*E ; G = 1.164*C - 0.392*D - 0.813*E ; B = 1.164*C + 2.017*D
    """
    c, d, e = y8 - 16, u8 - 128, v8 - 128
    return (
        _clamp8(int(round(1.164 * c + 1.596 * e))),
        _clamp8(int(round(1.164 * c - 0.392 * d - 0.813 * e))),
        _clamp8(int(round(1.164 * c + 2.017 * d))),
    )


def _near(a, b, tol=2):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# --------------------------------------------------------------------------
# 测试项
# --------------------------------------------------------------------------
def test_expected_size():
    """expectedSize 必须和 Kotlin 一致：低清 259200，高清 518400。"""
    assert expected_size(720, 480, True) == 259200, expected_size(720, 480, True)
    assert expected_size(720, 480, False) == 518400, expected_size(720, 480, False)
    assert expected_size(720, 480, True) == LOW_BODY
    assert expected_size(720, 480, False) == HD_BODY
    return "low=259200 / high=518400（与 Nv12Decoder.kt 一致）"


def test_layout_planes():
    """低清：Y=(720/2)*(480/2)=86400，UV 偏移=86400，86400+172800=259200；高清同理。"""
    lo = layout_for(True)
    assert (lo.y_w, lo.y_h) == (360, 240), lo.describe()
    assert lo.y_plane == (720 // 2) * (480 // 2) == 86400, lo.describe()
    assert lo.uv_offset == lo.y_plane == 86400, lo.describe()
    assert lo.uv_row_stride == (720 // 2) * 2 == 720, lo.describe()
    assert lo.uv_rows == 240 and lo.uv_bytes == 172800, lo.describe()
    assert lo.uv_offset + lo.uv_bytes == 259200 == lo.body, lo.describe()

    hd = layout_for(False)
    assert (hd.y_w, hd.y_h) == (720, 480) and hd.y_plane == 345600, hd.describe()
    assert hd.uv_offset == 345600 and hd.uv_bytes == 172800, hd.describe()
    assert hd.uv_offset + hd.uv_bytes == 518400 == hd.body, hd.describe()

    # 两种布局都必须满足：Y 字节数 + UV 字节数 == body.length
    for lay in (lo, hd):
        assert lay.y_plane + lay.uv_bytes == lay.body == expected_size(720, 480, lay.low)
    return "低清 86400+172800=259200；高清 345600+172800=518400"


def test_length_detection():
    """518400 -> 高清，259200 -> 低清，非法长度（含当年算错的 194400）报错。"""
    assert detect_layout(HD_BODY).low is False
    assert detect_layout(LOW_BODY).low is True
    try:
        detect_layout(194400)          # 当年算错的那个“有意义区”长度必须不被接受
    except ValueError:
        pass
    else:
        raise AssertionError("194400 被当成了合法长度")
    try:
        detect_layout(12345)
    except ValueError:
        pass
    else:
        raise AssertionError("非法长度没有报错")
    return "194400（当年算错的那个数）不会被误判为合法帧"


def test_hd_black_white():
    """高清纯黑 / 纯白，四角与中心都抽样。"""
    w, h, px = decode(make_test_frame(False), low=False)
    assert (w, h) == (720, 480) and len(px) == 720 * 480 * 3, (w, h, len(px))
    for (x, y) in ((0, 0), (719, 0), (0, 479), (719, 479), (360, 240)):
        assert pixel(px, x, y) == (0, 0, 0), (x, y, pixel(px, x, y))
    w, h, px = decode(make_test_frame(False, fill=(235, 128, 128)), low=False)
    assert pixel(px, 719, 479) == (255, 255, 255), pixel(px, 719, 479)
    return "Y=16->(0,0,0)；Y=235->(255,255,255)"


def test_colors_match_reference():
    """高清色块：整数公式与 BT.601 浮点参考一致（容差 2）。"""
    cases = [(235, 128, 240), (16, 240, 128), (16, 128, 240), (81, 90, 240), (145, 54, 34)]
    patches = [(20 * i + 10, 40, 20, 20, y8, u8, v8) for i, (y8, u8, v8) in enumerate(cases)]
    w, h, px = decode(make_test_frame(False, patches=patches), low=False)
    for i, (y8, u8, v8) in enumerate(cases):
        got = pixel(px, 20 * i + 20, 50)
        want = rgb_reference(y8, u8, v8)
        assert _near(got, want, 2), "第%d块 YUV=%s got=%s want=%s" % (i, (y8, u8, v8), got, want)
    return "%d 个色块与浮点参考一致" % len(cases)


def test_low_output_size():
    """低清解码后输出仍然是 720x480（不是 360x240）。"""
    body = make_test_frame(True)
    assert len(body) == LOW_BODY, len(body)
    w, h, px = decode(body, low=True)
    assert (w, h) == (720, 480), (w, h)
    assert len(px) == 720 * 480 * 3, len(px)
    return "低清 Body 259200 -> 输出 720x480"


def test_low_bounds_exhaustive():
    """
    边界回归（就是当初 length=259200; Index=259200 那个崩溃）：
    遍历所有输出像素，yIdx 与 uvIdx+1 都必须落在 Body 内；并且最后一个字节正好被用到。
    """
    for low in (False, True):
        lay = layout_for(low)
        n = lay.body
        for y in range(lay.out_h):
            for x in range(lay.out_w):
                yi = lay.y_index(x, y)
                ui = lay.uv_index(x, y)
                if yi >= n or ui + 1 >= n:
                    raise AssertionError("%s 越界 (%d,%d) yIdx=%d uvIdx=%d len=%d"
                                         % (lay.describe(), x, y, yi, ui, n))
        # 正好用满：右下角像素踩在最后一个字节上
        assert lay.max_y_index() == lay.y_plane - 1, (lay.describe(), lay.max_y_index())
        assert lay.max_uv_index() + 1 == lay.body - 1, (lay.describe(), lay.max_uv_index())
    lo = layout_for(True)
    return "低清 max yIdx=%d(=%d-1) uvIdx+1=%d(=%d-1)" % (
        lo.max_y_index(), lo.y_plane, lo.max_uv_index() + 1, lo.body)


def test_low_y_half_resolution_regression():
    """
    回归测试：低清 Y 是半分辨率（360x240），不能沿用高清的 y*720 跨度。

    旧 bug 用 yIdx = y*720 + x 解低清 Body：第 360 行起
    yIdx = 360*720 = 259200 == len(body)，真机抛
    ArrayIndexOutOfBoundsException: length=259200; Index=259200。
    """
    body = make_test_frame(True)
    n = len(body)
    assert n == 259200

    def old_buggy_y_pass():
        y_w = 720                              # 旧代码：低清也用高清跨度
        for y in range(480):
            for x in range(y_w):
                idx = y * y_w + x
                if idx >= n:
                    raise IndexError("Index=%d length=%d" % (idx, n))
                body[idx]

    try:
        old_buggy_y_pass()
    except IndexError as exc:
        assert "Index=259200 length=259200" in str(exc), str(exc)
    else:
        raise AssertionError("旧写法居然没越界，回归测试失效")

    # 布局守卫也要认出「低清 Body 套高清布局」，并在报错里点出 259200
    try:
        _decode_layout(body, layout_for(False), strict=True)
    except (ValueError, IndexError) as exc:
        assert "259200" in str(exc), str(exc)
    else:
        raise AssertionError("错配布局没有报错")

    # 正确布局必须能一路解到底
    w, h, px = decode(body, low=True)
    assert (w, h) == (720, 480) and len(px) == 720 * 480 * 3
    lay = layout_for(True)
    assert all(lay.y_index(x, y) < n and lay.uv_index(x, y) + 1 < n
               for y in range(0, 480, 7) for x in range(0, 720, 7))
    return "旧写法在 yIdx=259200 越界；正确布局解完 720x480"


def test_low_uv_row_stride_is_720():
    """低清 UV 行跨度是 720 字节（不是 Y 行跨度 360，也不要再减半成 180 对）。"""
    lo = layout_for(True)
    assert lo.y_w == 360 and lo.uv_row_stride == 720, lo.describe()
    assert lo.uv_w == 360, lo.describe()                  # U/V 对数是 360，不是 180
    assert lo.uv_row_stride != lo.y_w, "低清 UV 行跨度不能等于 Y 行跨度"
    assert lo.uv_bytes == 2 * lo.y_plane, lo.describe()   # UV 字节数 = 2 * Y 字节数
    return "低清 Y 行 360 字节 / UV 行 720 字节；UV 字节 = 2*Y 字节"


def test_low_color_patch():
    """低清色块：输出坐标 (60,40)-(180,120) 的色块要解出正确 RGB，块外保持底色。"""
    y8, u8, v8 = 81, 90, 240                      # 偏蓝的青色
    want = rgb_reference(y8, u8, v8)
    body = make_test_frame(True, patches=[(60, 40, 120, 80, y8, u8, v8)])
    w, h, px = decode(body, low=True)
    for (x, y) in ((62, 42), (120, 80), (178, 118)):
        got = pixel(px, x, y)
        assert _near(got, want, 2), "(%d,%d) got=%s want=%s" % (x, y, got, want)
    assert pixel(px, 300, 300) == (0, 0, 0), pixel(px, 300, 300)
    # 色块落在 Y 平面/UV 平面的哪些字节：确认为低清那套减半索引
    lay = layout_for(True)
    assert lay.y_index(62, 42) == (42 // 2) * 360 + (62 // 2), lay.describe()
    assert lay.uv_index(62, 42) == 86400 + (42 // 2) * 720 + (62 // 2) * 2, lay.describe()
    return "低清色块 YUV=%s -> %s" % ((y8, u8, v8), want)


def test_low_last_pixel_bytes():
    """低清最后一个像素必须落在 Body 的最后一个字节上（差一个字节就越界）。"""
    lay = layout_for(True)
    body = make_test_frame(True, patches=[(718, 478, 2, 2, 235, 128, 128)])
    w, h, px = decode(body, low=True)
    assert lay.max_uv_index() + 1 == len(body) - 1, lay.describe()
    assert pixel(px, 719, 479) == (255, 255, 255), pixel(px, 719, 479)
    return "右下角 2x2 色块 -> (255,255,255)，uvIdx 踩满最后一个字节"


# --------------------------------------------------------------------------
# 真机 dump 校验（离线）
# --------------------------------------------------------------------------
def probe_body_file(path, log=print):
    """拿一段真机 Body dump，校验布局假设（长度、各平面是否落在预期位置）。"""
    with open(path, "rb") as fp:
        body = fp.read()
    n = len(body)
    log("文件 %s：%d 字节" % (path, n))
    try:
        lay = detect_layout(n)
    except ValueError as exc:
        log("长度不认识：%s" % exc)
        return 2
    log("判定：%s" % lay.describe())
    log("expectedSize(720,480,low=%s) = %d" % (lay.low, expected_size(720, 480, lay.low)))

    def neutral(lo, hi):
        blk = body[lo:hi]
        return (sum(1 for b in blk if 120 <= b <= 136) / len(blk)) if blk else 0.0

    log("色度中性（0x78-0x88）占比：")
    log("  Y  平面 [%7d,%7d) = %.3f   <- 画面内容，应偏低" % (0, lay.y_plane, neutral(0, lay.y_plane)))
    log("  UV 平面 [%7d,%7d) = %.3f   <- 大量 0x80 中性色度，应偏高"
        % (lay.uv_offset, n, neutral(lay.uv_offset, n)))
    log("对照（当年写错的两套假设）：")
    log("  Y 360x360=129600 + UV 180x180x2=64800 -> 有意义区 194400，中性占比 %.3f"
        % neutral(129600, 194400))
    log("  UV 若从 172800 开始（360x480 说）      -> 中性占比 %.3f" % neutral(172800, n))
    if neutral(lay.uv_offset, n) > neutral(0, lay.y_plane):
        log("=> 符合：UV 平面的中性占比明显高于 Y 平面")
    else:
        log("=> 不太典型：画面可能太花，建议对着白墙再 dump 一帧")
    return 0


def dump_ppm(directory):
    """把测试帧解码结果写成 PPM，方便肉眼确认（可选）。"""
    os.makedirs(directory, exist_ok=True)
    written = []
    for low, name in ((False, "hd"), (True, "low")):
        body = make_test_frame(low, patches=[(60, 40, 200, 120, 200, 90, 200)])
        w, h, px = decode(body, low=low)
        path = os.path.join(directory, "nv12_%s.ppm" % name)
        with open(path, "wb") as fp:
            fp.write(b"P6\n%d %d\n255\n" % (w, h))
            fp.write(px)
        written.append(path)
    return written


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    def take(flag):
        if flag in argv:
            i = argv.index(flag)
            val = argv[i + 1] if i + 1 < len(argv) else None
            del argv[i:i + 2]
            return val
        return None

    dump_dir = take("--dump-ppm")
    probe_path = take("--probe")
    if argv:
        print("用法: python verify_nv12.py [--probe BODY.bin] [--dump-ppm DIR]")
        return 2
    if probe_path:
        return probe_body_file(probe_path)

    names = [n for n in sorted(globals()) if n.startswith("test_")]
    print("=== NX500 NV12 布局验证（高清 %d / 低清 %d）===" % (HD_BODY, LOW_BODY))
    print("    %s" % layout_for(False).describe())
    print("    %s" % layout_for(True).describe())
    failed = 0
    for name in names:
        fn = globals()[name]
        try:
            detail = fn()
        except Exception as exc:                       # noqa: BLE001 - 验证脚本要打印任何失败
            failed += 1
            print("FAIL  %-40s %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("PASS  %-40s %s" % (name, detail or ""))

    if dump_dir:
        for path in dump_ppm(dump_dir):
            print("DUMP  %s" % path)

    total = len(names)
    print("--- %d/%d 通过%s" % (total - failed, total, "" if not failed else "，%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
