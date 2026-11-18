package org.mangafon;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.RectF;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLExt;
import android.opengl.EGLSurface;
import android.opengl.GLES20;
import android.opengl.GLUtils;
import android.view.Surface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public class VideoMakerHelper {
    public static volatile int progressPercent = 0;
    public static volatile String progressText = "";
    public static volatile boolean cancelRequested = false;

    private static final int EGL_RECORDABLE_ANDROID = 0x3142;

    private static class TimingItem {
        String filename;
        double start;
        double end;
    }

    private static class WavInfo {
        int audioFormat;
        int channels;
        int sampleRate;
        int bitsPerSample;
        long dataOffset;
        long dataSize;
    }

    public static void requestCancel() {
        cancelRequested = true;
    }

    public static void createVideo(
            String framesDir,
            String timingsJsonPath,
            String audioPath,
            String outMp4Path,
            int width,
            int height,
            int fps,
            boolean kenBurns
    ) throws Exception {
        cancelRequested = false;
        progressPercent = 0;
        progressText = "Чтение таймингов...";

        List<TimingItem> items = readTimings(timingsJsonPath, framesDir);
        if (items.isEmpty()) {
            throw new RuntimeException("В timings.json нет кадров");
        }

        File outFile = new File(outMp4Path);
        File parentDir = outFile.getParentFile();
        File tmpAudio = new File(parentDir, "_tmp_audio.m4a");
        File tmpVideo = new File(parentDir, "_tmp_video.mp4");
        if (tmpAudio.exists()) tmpAudio.delete();
        if (tmpVideo.exists()) tmpVideo.delete();

        try {
            progressText = "Кодирование звука (AAC)...";
            progressPercent = 2;
            encodeAudioToAac(audioPath, tmpAudio.getAbsolutePath());
            if (cancelRequested) throw new RuntimeException("Сборка отменена пользователем");

            progressText = "Рендер видео (H.264)...";
            progressPercent = 15;
            encodeVideoH264(framesDir, items, tmpVideo.getAbsolutePath(), width, height, fps, kenBurns);
            if (cancelRequested) throw new RuntimeException("Сборка отменена пользователем");

            progressText = "Финальная склейка MP4...";
            progressPercent = 95;
            muxVideoAndAudio(tmpVideo.getAbsolutePath(), tmpAudio.getAbsolutePath(), outFile.getAbsolutePath());

            progressPercent = 100;
            progressText = "Видео готово!";
        } finally {
            if (tmpAudio.exists()) tmpAudio.delete();
            if (tmpVideo.exists()) tmpVideo.delete();
        }
    }

    private static List<TimingItem> readTimings(String jsonPath, String framesDir) throws Exception {
        StringBuilder sb = new StringBuilder();
        try (BufferedInputStream bis = new BufferedInputStream(new FileInputStream(jsonPath))) {
            byte[] buf = new byte[8192];
            int r;
            while ((r = bis.read(buf)) > 0) {
                sb.append(new String(buf, 0, r, StandardCharsets.UTF_8));
            }
        }
        JSONArray arr = new JSONArray(sb.toString());
        List<TimingItem> list = new ArrayList<>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject obj = arr.getJSONObject(i);
            TimingItem it = new TimingItem();
            it.filename = obj.getString("filename");
            it.start = obj.getDouble("start");
            it.end = obj.getDouble("end");
            File f = new File(framesDir, it.filename);
            if (!f.isFile()) {
                throw new RuntimeException("Не найден файл кадра: " + f.getAbsolutePath());
            }
            list.add(it);
        }
        return list;
    }

    // ---------- 1. АУДИО -> AAC (.m4a) ----------
    private static void encodeAudioToAac(String audioPath, String outM4aPath) throws Exception {
        final int targetRate = 44100;
        WavInfo wav = null;
        if (audioPath.toLowerCase(Locale.ROOT).endsWith(".wav")) {
            wav = parseWav(audioPath);
        }

        int outChannels = (wav != null && wav.channels == 2) ? 2 : 1;
        if (wav == null) {
            outChannels = detectChannelsWithExtractor(audioPath);
        }

        MediaFormat aacFmt = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, targetRate, outChannels);
        aacFmt.setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC);
        aacFmt.setInteger(MediaFormat.KEY_BIT_RATE, 128000);
        aacFmt.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 32768);

        MediaCodec aacCodec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC);
        MediaMuxer muxer = new MediaMuxer(outM4aPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        int[] trackHolder = new int[]{-1};
        boolean[] muxerStarted = new boolean[]{false};
        long[] totalOutSamples = new long[]{0L};

        try {
            aacCodec.configure(aacFmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            aacCodec.start();

            if (wav != null && wav.audioFormat == 1 && wav.bitsPerSample == 16 && (wav.channels == 1 || wav.channels == 2)) {
                int frameBytes = wav.channels * 2;
                try (BufferedInputStream bis = new BufferedInputStream(new FileInputStream(audioPath), 65536)) {
                    skipFully(bis, wav.dataOffset);
                    byte[] inBuf = new byte[4096 * frameBytes];
                    long bytesLeft = wav.dataSize;
                    long bytesDone = 0;

                    while (bytesLeft > 0) {
                        if (cancelRequested) break;
                        int toRead = (int) Math.min(inBuf.length, bytesLeft);
                        int read = bis.read(inBuf, 0, toRead);
                        if (read <= 0) break;
                        bytesLeft -= read;
                        bytesDone += read;

                        int inFrames = read / frameBytes;
                        short[] pcm = bytesToShorts(inBuf, inFrames * wav.channels);
                        short[] resampled = resampleInterleaved(pcm, inFrames, wav.channels, wav.sampleRate, targetRate, outChannels);
                        byte[] outBytes = shortsToBytes(resampled);
                        feedPcmToAac(aacCodec, muxer, trackHolder, muxerStarted, outBytes, outChannels, targetRate, totalOutSamples);

                        int pct = 2 + (int) ((bytesDone * 12L) / Math.max(1L, wav.dataSize));
                        progressPercent = Math.min(14, pct);
                    }
                }
            } else {
                decodeOtherAudioToAac(audioPath, aacCodec, muxer, trackHolder, muxerStarted, targetRate, outChannels, totalOutSamples);
            }

            // Сигнал конца аудио
            while (true) {
                int inIdx = aacCodec.dequeueInputBuffer(10000);
                if (inIdx >= 0) {
                    long ptsUs = (totalOutSamples[0] * 1_000_000L) / targetRate;
                    aacCodec.queueInputBuffer(inIdx, 0, 0, ptsUs, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                    break;
                }
                drainEncoder(aacCodec, muxer, trackHolder, muxerStarted, false);
            }
            drainEncoder(aacCodec, muxer, trackHolder, muxerStarted, true);
        } finally {
            try { aacCodec.stop(); } catch (Exception ignored) {}
            try { aacCodec.release(); } catch (Exception ignored) {}
            if (muxerStarted[0]) {
                try { muxer.stop(); } catch (Exception ignored) {}
            }
            try { muxer.release(); } catch (Exception ignored) {}
        }
    }

    private static int detectChannelsWithExtractor(String audioPath) throws Exception {
        MediaExtractor ex = new MediaExtractor();
        try {
            ex.setDataSource(audioPath);
            for (int i = 0; i < ex.getTrackCount(); i++) {
                MediaFormat f = ex.getTrackFormat(i);
                String mime = f.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("audio/")) {
                    int ch = f.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                    return (ch >= 2) ? 2 : 1;
                }
            }
            return 1;
        } finally {
            ex.release();
        }
    }

    private static void decodeOtherAudioToAac(
            String audioPath,
            MediaCodec aacCodec,
            MediaMuxer muxer,
            int[] trackHolder,
            boolean[] muxerStarted,
            int targetRate,
            int outChannels,
            long[] totalOutSamples
    ) throws Exception {
        MediaExtractor ex = new MediaExtractor();
        MediaCodec dec = null;
        try {
            ex.setDataSource(audioPath);
            int trackIdx = -1;
            MediaFormat fmt = null;
            for (int i = 0; i < ex.getTrackCount(); i++) {
                MediaFormat f = ex.getTrackFormat(i);
                String mime = f.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("audio/")) {
                    trackIdx = i;
                    fmt = f;
                    break;
                }
            }
            if (trackIdx < 0 || fmt == null) throw new RuntimeException("Аудиодорожка не найдена");
            ex.selectTrack(trackIdx);

            String mime = fmt.getString(MediaFormat.KEY_MIME);
            int srcRate = fmt.getInteger(MediaFormat.KEY_SAMPLE_RATE);
            int srcCh = fmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT);

            dec = MediaCodec.createDecoderByType(mime);
            dec.configure(fmt, null, null, 0);
            dec.start();

            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            boolean inEOS = false;
            boolean outEOS = false;
            while (!outEOS && !cancelRequested) {
                if (!inEOS) {
                    int inIdx = dec.dequeueInputBuffer(10000);
                    if (inIdx >= 0) {
                        ByteBuffer ib = dec.getInputBuffer(inIdx);
                        int sz = ex.readSampleData(ib, 0);
                        if (sz < 0) {
                            dec.queueInputBuffer(inIdx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inEOS = true;
                        } else {
                            dec.queueInputBuffer(inIdx, 0, sz, ex.getSampleTime(), 0);
                            ex.advance();
                        }
                    }
                }
                int outIdx = dec.dequeueOutputBuffer(info, 10000);
                if (outIdx >= 0) {
                    if (info.size > 0) {
                        ByteBuffer ob = dec.getOutputBuffer(outIdx);
                        ob.position(info.offset);
                        ob.limit(info.offset + info.size);
                        byte[] raw = new byte[info.size];
                        ob.get(raw);
                        int inFrames = (info.size / 2) / Math.max(1, srcCh);
                        short[] pcm = bytesToShorts(raw, inFrames * srcCh);
                        short[] res = resampleInterleaved(pcm, inFrames, srcCh, srcRate, targetRate, outChannels);
                        feedPcmToAac(aacCodec, muxer, trackHolder, muxerStarted, shortsToBytes(res), outChannels, targetRate, totalOutSamples);
                    }
                    dec.releaseOutputBuffer(outIdx, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) outEOS = true;
                } else if (outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat nf = dec.getOutputFormat();
                    if (nf.containsKey(MediaFormat.KEY_SAMPLE_RATE)) srcRate = nf.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                    if (nf.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) srcCh = nf.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                }
            }
        } finally {
            if (dec != null) {
                try { dec.stop(); } catch (Exception ignored) {}
                try { dec.release(); } catch (Exception ignored) {}
            }
            ex.release();
        }
    }

    private static void feedPcmToAac(
            MediaCodec aacCodec,
            MediaMuxer muxer,
            int[] trackHolder,
            boolean[] muxerStarted,
            byte[] pcmBytes,
            int channels,
            int sampleRate,
            long[] totalOutSamples
    ) {
        int offset = 0;
        int frameBytes = channels * 2;
        while (offset < pcmBytes.length) {
            int inIdx = aacCodec.dequeueInputBuffer(10000);
            if (inIdx >= 0) {
                ByteBuffer inBuf = aacCodec.getInputBuffer(inIdx);
                inBuf.clear();
                int toCopy = Math.min(pcmBytes.length - offset, inBuf.remaining());
                toCopy -= (toCopy % frameBytes);
                if (toCopy <= 0) break;
                inBuf.put(pcmBytes, offset, toCopy);
                long ptsUs = (totalOutSamples[0] * 1_000_000L) / sampleRate;
                aacCodec.queueInputBuffer(inIdx, 0, toCopy, ptsUs, 0);
                offset += toCopy;
                totalOutSamples[0] += (toCopy / frameBytes);
            }
            drainEncoder(aacCodec, muxer, trackHolder, muxerStarted, false);
        }
    }

    // ---------- 2. ВИДЕО (OpenGL + MediaCodec H.264) ----------
    private static void encodeVideoH264(
            String framesDir,
            List<TimingItem> items,
            String outMp4Path,
            int width,
            int height,
            int fps,
            boolean kenBurns
    ) throws Exception {
        MediaFormat vFmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
        vFmt.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        vFmt.setInteger(MediaFormat.KEY_BIT_RATE, (width >= 1920) ? 6_000_000 : 3_500_000);
        vFmt.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
        vFmt.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2);

        MediaCodec vCodec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
        MediaMuxer muxer = new MediaMuxer(outMp4Path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        int[] trackHolder = new int[]{-1};
        boolean[] muxerStarted = new boolean[]{false};

        Surface inputSurface = null;
        EGLDisplay eglDisplay = EGL14.EGL_NO_DISPLAY;
        EGLContext eglContext = EGL14.EGL_NO_CONTEXT;
        EGLSurface eglSurface = EGL14.EGL_NO_SURFACE;

        try {
            vCodec.configure(vFmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            inputSurface = vCodec.createInputSurface();
            vCodec.start();

            // Инициализация EGL
            eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
            if (eglDisplay == EGL14.EGL_NO_DISPLAY) throw new RuntimeException("Нет EGLDisplay");
            int[] ver = new int[2];
            if (!EGL14.eglInitialize(eglDisplay, ver, 0, ver, 1)) throw new RuntimeException("Ошибка eglInitialize");

            int[] cfgAttribs = {
                    EGL14.EGL_RED_SIZE, 8,
                    EGL14.EGL_GREEN_SIZE, 8,
                    EGL14.EGL_BLUE_SIZE, 8,
                    EGL14.EGL_ALPHA_SIZE, 8,
                    EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                    EGL_RECORDABLE_ANDROID, 1,
                    EGL14.EGL_NONE
            };
            EGLConfig[] configs = new EGLConfig[1];
            int[] numCfg = new int[1];
            if (!EGL14.eglChooseConfig(eglDisplay, cfgAttribs, 0, configs, 0, 1, numCfg, 0) || numCfg[0] == 0) {
                throw new RuntimeException("Не найден EGLConfig для видео");
            }

            int[] ctxAttribs = {EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE};
            eglContext = EGL14.eglCreateContext(eglDisplay, configs[0], EGL14.EGL_NO_CONTEXT, ctxAttribs, 0);
            int[] surfAttribs = {EGL14.EGL_NONE};
            eglSurface = EGL14.eglCreateWindowSurface(eglDisplay, configs[0], inputSurface, surfAttribs, 0);
            if (!EGL14.eglMakeCurrent(eglDisplay, eglSurface, eglSurface, eglContext)) {
                throw new RuntimeException("Ошибка eglMakeCurrent");
            }

            // Шейдер OpenGL ES 2.0 для рисования текстуры
            int program = buildGlProgram();
            int aPos = GLES20.glGetAttribLocation(program, "aPos");
            int aTex = GLES20.glGetAttribLocation(program, "aTex");
            int uTex = GLES20.glGetUniformLocation(program, "uTex");

            float[] quadCoords = {
                    -1f,  1f,
                    -1f, -1f,
                     1f, -1f,
                     1f,  1f
            };
            FloatBuffer posBuf = ByteBuffer.allocateDirect(quadCoords.length * 4)
                    .order(ByteOrder.nativeOrder()).asFloatBuffer();
            posBuf.put(quadCoords).position(0);

            FloatBuffer texBuf = ByteBuffer.allocateDirect(8 * 4)
                    .order(ByteOrder.nativeOrder()).asFloatBuffer();

            GLES20.glViewport(0, 0, width, height);
            GLES20.glUseProgram(program);

            double totalDur = items.get(items.size() - 1).end;
            long totalFrames = Math.max(1L, Math.round(totalDur * fps));
            long prevEndFrame = 0L;

            for (int idx = 0; idx < items.size(); idx++) {
                if (cancelRequested) break;
                TimingItem item = items.get(idx);
                long startFrame = prevEndFrame;
                long endFrame = (idx == items.size() - 1)
                        ? Math.max(startFrame + 1, totalFrames)
                        : Math.max(startFrame + 1, Math.round(item.end * fps));
                prevEndFrame = endFrame;

                File imgFile = new File(framesDir, item.filename);
                Bitmap composed = composeFrameBitmap(imgFile.getAbsolutePath(), width, height);
                int texId = uploadBitmapToTexture(composed);
                composed.recycle();

                long frameSpan = Math.max(1L, endFrame - startFrame);
                for (long f = startFrame; f < endFrame; f++) {
                    if (cancelRequested) break;

                    float zoom = 1.0f;
                    if (kenBurns) {
                        double t = (frameSpan > 1) ? ((double) (f - startFrame) / (double) (frameSpan - 1)) : 0.0;
                        zoom = (idx % 2 == 0)
                                ? (1.0f + 0.045f * (float) t)
                                : (1.045f - 0.045f * (float) t);
                    }
                    float half = 0.5f / zoom;
                    float u0 = 0.5f - half;
                    float u1 = 0.5f + half;
                    float v0 = 0.5f - half;
                    float v1 = 0.5f + half;

                    float[] uv = {
                            u0, v0,
                            u0, v1,
                            u1, v1,
                            u1, v0
                    };
                    texBuf.clear();
                    texBuf.put(uv).position(0);

                    GLES20.glClearColor(0f, 0f, 0f, 1f);
                    GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT);

                    GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
                    GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texId);
                    GLES20.glUniform1i(uTex, 0);

                    GLES20.glEnableVertexAttribArray(aPos);
                    GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 0, posBuf);
                    GLES20.glEnableVertexAttribArray(aTex);
                    GLES20.glVertexAttribPointer(aTex, 2, GLES20.GL_FLOAT, false, 0, texBuf);

                    GLES20.glDrawArrays(GLES20.GL_TRIANGLE_FAN, 0, 4);

                    long ptsNs = (f * 1_000_000_000L) / fps;
                    EGLExt.eglPresentationTimeANDROID(eglDisplay, eglSurface, ptsNs);
                    EGL14.eglSwapBuffers(eglDisplay, eglSurface);

                    drainEncoder(vCodec, muxer, trackHolder, muxerStarted, false);

                    if ((f % 5) == 0) {
                        int pct = 15 + (int) ((f * 80L) / totalFrames);
                        progressPercent = Math.min(94, pct);
                        progressText = String.format(Locale.US, "Рендер видео: %d%% (кадр %d/%d)", progressPercent, idx + 1, items.size());
                    }
                }
                GLES20.glDeleteTextures(1, new int[]{texId}, 0);
            }

            vCodec.signalEndOfInputStream();
            drainEncoder(vCodec, muxer, trackHolder, muxerStarted, true);
        } finally {
            if (eglDisplay != EGL14.EGL_NO_DISPLAY) {
                EGL14.eglMakeCurrent(eglDisplay, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
                if (eglSurface != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(eglDisplay, eglSurface);
                if (eglContext != EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(eglDisplay, eglContext);
                EGL14.eglReleaseThread();
                EGL14.eglTerminate(eglDisplay);
            }
            if (inputSurface != null) inputSurface.release();
            try { vCodec.stop(); } catch (Exception ignored) {}
            try { vCodec.release(); } catch (Exception ignored) {}
            if (muxerStarted[0]) {
                try { muxer.stop(); } catch (Exception ignored) {}
            }
            try { muxer.release(); } catch (Exception ignored) {}
        }
    }

    private static Bitmap composeFrameBitmap(String path, int outW, int outH) {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(path, bounds);

        int sample = 1;
        while ((bounds.outWidth / sample) > outW * 2 && (bounds.outHeight / sample) > outH * 2) {
            sample *= 2;
        }
        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inSampleSize = sample;
        Bitmap src = BitmapFactory.decodeFile(path, opts);
        if (src == null) {
            throw new RuntimeException("Не удалось декодировать кадр: " + path);
        }

        Bitmap out = Bitmap.createBitmap(outW, outH, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(out);
        canvas.drawColor(Color.rgb(16, 18, 24));

        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);

        // Размытый тёмный фон (быстрый даунскейл в мини-картинку и апскейл)
        int sw = src.getWidth();
        int sh = src.getHeight();
        float coverScale = Math.max((float) outW / sw, (float) outH / sh);
        int bgW = 96;
        int bgH = 54;
        Bitmap tiny = Bitmap.createBitmap(bgW, bgH, Bitmap.Config.ARGB_8888);
        Canvas tinyCanvas = new Canvas(tiny);
        float tinyScale = Math.max((float) bgW / sw, (float) bgH / sh);
        float tw = sw * tinyScale;
        float th = sh * tinyScale;
        tinyCanvas.drawBitmap(src, null, new RectF((bgW - tw) / 2f, (bgH - th) / 2f, (bgW + tw) / 2f, (bgH + th) / 2f), paint);
        canvas.drawBitmap(tiny, null, new Rect(0, 0, outW, outH), paint);
        tiny.recycle();

        // Затемнение фона
        canvas.drawColor(Color.argb(180, 10, 12, 18));

        // Основной чёткий кадр по центру
        float padX = 36f;
        float padY = 24f;
        float fitScale = Math.min((outW - padX * 2f) / sw, (outH - padY * 2f) / sh);
        float dw = sw * fitScale;
        float dh = sh * fitScale;
        float left = (outW - dw) / 2f;
        float top = (outH - dh) / 2f;
        RectF dstRect = new RectF(left, top, left + dw, top + dh);
        canvas.drawBitmap(src, null, dstRect, paint);

        Paint border = new Paint(Paint.ANTI_ALIAS_FLAG);
        border.setStyle(Paint.Style.STROKE);
        border.setStrokeWidth(3f);
        border.setColor(Color.rgb(70, 78, 92));
        canvas.drawRect(dstRect, border);

        src.recycle();
        return out;
    }

    private static int uploadBitmapToTexture(Bitmap bmp) {
        int[] tex = new int[1];
        GLES20.glGenTextures(1, tex, 0);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, tex[0]);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
        GLUtils.texImage2D(GLES20.GL_TEXTURE_2D, 0, bmp, 0);
        return tex[0];
    }

    private static int buildGlProgram() {
        String vsrc =
                "attribute vec2 aPos;\n" +
                "attribute vec2 aTex;\n" +
                "varying vec2 vTex;\n" +
                "void main() {\n" +
                "  gl_Position = vec4(aPos, 0.0, 1.0);\n" +
                "  vTex = aTex;\n" +
                "}\n";
        String fsrc =
                "precision mediump float;\n" +
                "varying vec2 vTex;\n" +
                "uniform sampler2D uTex;\n" +
                "void main() {\n" +
                "  gl_FragColor = texture2D(uTex, vTex);\n" +
                "}\n";
        int vs = compileShader(GLES20.GL_VERTEX_SHADER, vsrc);
        int fs = compileShader(GLES20.GL_FRAGMENT_SHADER, fsrc);
        int prog = GLES20.glCreateProgram();
        GLES20.glAttachShader(prog, vs);
        GLES20.glAttachShader(prog, fs);
        GLES20.glLinkProgram(prog);
        return prog;
    }

    private static int compileShader(int type, String src) {
        int s = GLES20.glCreateShader(type);
        GLES20.glShaderSource(s, src);
        GLES20.glCompileShader(s);
        return s;
    }

    private static void drainEncoder(
            MediaCodec encoder,
            MediaMuxer muxer,
            int[] trackHolder,
            boolean[] muxerStarted,
            boolean endOfStream
    ) {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (true) {
            int outIdx = encoder.dequeueOutputBuffer(info, endOfStream ? 10000 : 0);
            if (outIdx == MediaCodec.INFO_TRY_AGAIN_LATER) {
                if (!endOfStream) break;
            } else if (outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                if (!muxerStarted[0]) {
                    trackHolder[0] = muxer.addTrack(encoder.getOutputFormat());
                    muxer.start();
                    muxerStarted[0] = true;
                }
            } else if (outIdx >= 0) {
                ByteBuffer data = encoder.getOutputBuffer(outIdx);
                if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                    info.size = 0;
                }
                if (info.size > 0 && muxerStarted[0] && data != null) {
                    data.position(info.offset);
                    data.limit(info.offset + info.size);
                    muxer.writeSampleData(trackHolder[0], data, info);
                }
                encoder.releaseOutputBuffer(outIdx, false);
                if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                    break;
                }
            }
        }
    }

    // ---------- 3. СБОРКА ВИДЕО + АУДИО В ОДИН MP4 ----------
    private static void muxVideoAndAudio(String videoPath, String audioPath, String outMp4Path) throws Exception {
        MediaExtractor vEx = new MediaExtractor();
        MediaExtractor aEx = new MediaExtractor();
        MediaMuxer muxer = new MediaMuxer(outMp4Path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        boolean started = false;
        try {
            vEx.setDataSource(videoPath);
            int vTrackIn = -1;
            MediaFormat vFmt = null;
            for (int i = 0; i < vEx.getTrackCount(); i++) {
                MediaFormat f = vEx.getTrackFormat(i);
                String m = f.getString(MediaFormat.KEY_MIME);
                if (m != null && m.startsWith("video/")) {
                    vTrackIn = i;
                    vFmt = f;
                    break;
                }
            }

            aEx.setDataSource(audioPath);
            int aTrackIn = -1;
            MediaFormat aFmt = null;
            for (int i = 0; i < aEx.getTrackCount(); i++) {
                MediaFormat f = aEx.getTrackFormat(i);
                String m = f.getString(MediaFormat.KEY_MIME);
                if (m != null && m.startsWith("audio/")) {
                    aTrackIn = i;
                    aFmt = f;
                    break;
                }
            }

            if (vTrackIn < 0 || vFmt == null) throw new RuntimeException("Видеодорожка не найдена");
            if (aTrackIn < 0 || aFmt == null) throw new RuntimeException("Аудиодорожка не найдена");

            vEx.selectTrack(vTrackIn);
            aEx.selectTrack(aTrackIn);

            int vTrackOut = muxer.addTrack(vFmt);
            int aTrackOut = muxer.addTrack(aFmt);
            muxer.start();
            started = true;

            ByteBuffer buf = ByteBuffer.allocate(1024 * 1024);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();

            long lastVPts = -1;
            while (true) {
                int sz = vEx.readSampleData(buf, 0);
                if (sz < 0) break;
                long pts = vEx.getSampleTime();
                if (pts <= lastVPts) pts = lastVPts + 1;
                lastVPts = pts;
                info.offset = 0;
                info.size = sz;
                info.presentationTimeUs = pts;
                info.flags = vEx.getSampleFlags();
                muxer.writeSampleData(vTrackOut, buf, info);
                vEx.advance();
            }

            long lastAPts = -1;
            while (true) {
                int sz = aEx.readSampleData(buf, 0);
                if (sz < 0) break;
                long pts = aEx.getSampleTime();
                if (pts <= lastAPts) pts = lastAPts + 1;
                lastAPts = pts;
                info.offset = 0;
                info.size = sz;
                info.presentationTimeUs = pts;
                info.flags = aEx.getSampleFlags();
                muxer.writeSampleData(aTrackOut, buf, info);
                aEx.advance();
            }
        } finally {
            vEx.release();
            aEx.release();
            if (started) {
                try { muxer.stop(); } catch (Exception ignored) {}
            }
            try { muxer.release(); } catch (Exception ignored) {}
        }
    }

    // ---------- ВСПОМОГАТЕЛЬНЫЕ ДЛЯ WAV ----------
    private static short[] bytesToShorts(byte[] bytes, int count) {
        short[] s = new short[count];
        ByteBuffer.wrap(bytes, 0, count * 2).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(s);
        return s;
    }

    private static byte[] shortsToBytes(short[] s) {
        byte[] b = new byte[s.length * 2];
        ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().put(s);
        return b;
    }

    private static short[] resampleInterleaved(short[] in, int inFrames, int inCh, int inRate, int outRate, int outCh) {
        if (inFrames <= 0) return new short[0];
        if (inRate == outRate && inCh == outCh) return in;

        int outFrames = (inRate == outRate) ? inFrames : Math.max(1, (int) Math.round(inFrames * ((double) outRate / inRate)));
        short[] out = new short[outFrames * outCh];
        double ratio = (double) inRate / outRate;

        for (int i = 0; i < outFrames; i++) {
            double srcPos = (inRate == outRate) ? i : (i * ratio);
            int idx = Math.min(inFrames - 1, (int) srcPos);
            double frac = srcPos - idx;
            int idx2 = Math.min(inFrames - 1, idx + 1);

            if (outCh == 1) {
                int s1 = (inCh == 1) ? in[idx] : ((in[idx * inCh] + in[idx * inCh + 1]) / 2);
                int s2 = (inCh == 1) ? in[idx2] : ((in[idx2 * inCh] + in[idx2 * inCh + 1]) / 2);
                out[i] = (short) (s1 + frac * (s2 - s1));
            } else {
                int l1 = in[idx * inCh];
                int r1 = (inCh >= 2) ? in[idx * inCh + 1] : l1;
                int l2 = in[idx2 * inCh];
                int r2 = (inCh >= 2) ? in[idx2 * inCh + 1] : l2;
                out[i * 2] = (short) (l1 + frac * (l2 - l1));
                out[i * 2 + 1] = (short) (r1 + frac * (r2 - r1));
            }
        }
        return out;
    }

    private static int readUInt16LE(InputStream is) throws Exception {
        int b0 = is.read();
        int b1 = is.read();
        if (b0 < 0 || b1 < 0) return -1;
        return (b0 & 0xFF) | ((b1 & 0xFF) << 8);
    }

    private static long readUInt32LE(InputStream is) throws Exception {
        int b0 = is.read();
        int b1 = is.read();
        int b2 = is.read();
        int b3 = is.read();
        if (b0 < 0 || b1 < 0 || b2 < 0 || b3 < 0) return -1;
        return (b0 & 0xFFL) | ((b1 & 0xFFL) << 8) | ((b2 & 0xFFL) << 16) | ((b3 & 0xFFL) << 24);
    }

    private static void skipFully(InputStream is, long n) throws Exception {
        long left = n;
        while (left > 0) {
            long skipped = is.skip(left);
            if (skipped <= 0) {
                if (is.read() < 0) break;
                left--;
            } else {
                left -= skipped;
            }
        }
    }

    private static WavInfo parseWav(String path) {
        try (BufferedInputStream bis = new BufferedInputStream(new FileInputStream(path))) {
            byte[] hdr = new byte[12];
            if (bis.read(hdr) != 12) return null;
            if (hdr[0] != 'R' || hdr[1] != 'I' || hdr[2] != 'F' || hdr[3] != 'F') return null;
            if (hdr[8] != 'W' || hdr[9] != 'A' || hdr[10] != 'V' || hdr[11] != 'E') return null;

            long pos = 12;
            WavInfo info = new WavInfo();
            boolean gotFmt = false;
            boolean gotData = false;

            while (true) {
                byte[] idBytes = new byte[4];
                if (bis.read(idBytes) < 4) break;
                long size = readUInt32LE(bis);
                if (size < 0) break;
                pos += 8;

                String id = new String(idBytes, StandardCharsets.US_ASCII);
                if ("fmt ".equals(id) && size >= 16) {
                    info.audioFormat = readUInt16LE(bis);
                    info.channels = readUInt16LE(bis);
                    info.sampleRate = (int) readUInt32LE(bis);
                    skipFully(bis, 6);
                    info.bitsPerSample = readUInt16LE(bis);
                    if (size > 16) skipFully(bis, size - 16);
                    gotFmt = true;
                } else if ("data".equals(id)) {
                    info.dataOffset = pos;
                    info.dataSize = size;
                    gotData = true;
                    break;
                } else {
                    skipFully(bis, size);
                }
                pos += size;
                if ((size & 1L) != 0) {
                    skipFully(bis, 1);
                    pos += 1;
                }
            }
            return (gotFmt && gotData && info.sampleRate > 0) ? info : null;
        } catch (Exception e) {
            return null;
        }
    }
}
