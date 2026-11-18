package org.mangafon;

import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;

import org.json.JSONArray;
import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStreamWriter;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

public class VoskHelper {
    public static volatile int progressPercent = 0;
    public static volatile String progressText = "";

    private static class WavInfo {
        int audioFormat;
        int channels;
        int sampleRate;
        int bitsPerSample;
        long dataOffset;
        long dataSize;
    }

    public static void recognizeAudio(String modelDir, String audioPath, String outJsonPath) throws Exception {
        progressPercent = 0;
        progressText = "Загрузка модели Vosk...";

        Model model = null;
        Recognizer recognizer = null;
        try {
            model = new Model(modelDir);
            recognizer = new Recognizer(model, 16000.0f);
            recognizer.setWords(true);

            JSONArray allWords = new JSONArray();
            double durationSec = 0.0;

            progressText = "Подготовка аудио...";
            WavInfo wav = null;
            if (audioPath.toLowerCase(Locale.ROOT).endsWith(".wav")) {
                wav = parseWav(audioPath);
            }

            if (wav != null && wav.audioFormat == 1 && wav.bitsPerSample == 16 && (wav.channels == 1 || wav.channels == 2)) {
                durationSec = processPcm16Wav(audioPath, wav, recognizer, allWords);
            } else {
                durationSec = processWithMediaCodec(audioPath, recognizer, allWords);
            }

            appendWordsFromJson(recognizer.getFinalResult(), allWords);

            JSONObject root = new JSONObject();
            root.put("duration", durationSec);
            root.put("words", allWords);

            try (OutputStreamWriter writer = new OutputStreamWriter(new FileOutputStream(outJsonPath), StandardCharsets.UTF_8)) {
                writer.write(root.toString());
            }

            progressPercent = 100;
            progressText = "Готово!";
        } finally {
            if (recognizer != null) {
                try { recognizer.close(); } catch (Exception ignored) {}
            }
            if (model != null) {
                try { model.close(); } catch (Exception ignored) {}
            }
        }
    }

    private static void appendWordsFromJson(String jsonStr, JSONArray target) {
        if (jsonStr == null || jsonStr.isEmpty()) return;
        try {
            JSONObject obj = new JSONObject(jsonStr);
            if (obj.has("result")) {
                JSONArray arr = obj.getJSONArray("result");
                for (int i = 0; i < arr.length(); i++) {
                    target.put(arr.getJSONObject(i));
                }
            }
        } catch (Exception ignored) {}
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
                int r = bis.read(idBytes);
                if (r < 4) break;
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

    private static double processPcm16Wav(String path, WavInfo wav, Recognizer recognizer, JSONArray allWords) throws Exception {
        int frameBytes = wav.channels * 2;
        long totalFrames = wav.dataSize / frameBytes;
        double durationSec = (double) totalFrames / wav.sampleRate;

        try (BufferedInputStream bis = new BufferedInputStream(new FileInputStream(path), 65536)) {
            skipFully(bis, wav.dataOffset);

            byte[] inBuf = new byte[8192 * frameBytes];
            long bytesLeft = wav.dataSize;
            long bytesDone = 0;

            while (bytesLeft > 0) {
                int toRead = (int) Math.min(inBuf.length, bytesLeft);
                int read = bis.read(inBuf, 0, toRead);
                if (read <= 0) break;
                bytesLeft -= read;
                bytesDone += read;

                int frames = read / frameBytes;
                short[] mono = new short[frames];
                ByteBuffer bb = ByteBuffer.wrap(inBuf, 0, frames * frameBytes).order(ByteOrder.LITTLE_ENDIAN);
                if (wav.channels == 1) {
                    for (int i = 0; i < frames; i++) {
                        mono[i] = bb.getShort();
                    }
                } else {
                    for (int i = 0; i < frames; i++) {
                        int l = bb.getShort();
                        int r = bb.getShort();
                        mono[i] = (short) ((l + r) / 2);
                    }
                }

                feedResampled16k(mono, frames, wav.sampleRate, recognizer, allWords);

                int pct = (int) Math.min(99, (bytesDone * 100L) / Math.max(1L, wav.dataSize));
                progressPercent = pct;
                progressText = "Распознаю речь: " + pct + "%";
            }
        }
        return durationSec;
    }

    private static double processWithMediaCodec(String path, Recognizer recognizer, JSONArray allWords) throws Exception {
        MediaExtractor extractor = new MediaExtractor();
        MediaCodec codec = null;
        double durationSec = 0.0;
        try {
            extractor.setDataSource(path);
            int trackIndex = -1;
            MediaFormat format = null;
            for (int i = 0; i < extractor.getTrackCount(); i++) {
                MediaFormat f = extractor.getTrackFormat(i);
                String mime = f.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("audio/")) {
                    trackIndex = i;
                    format = f;
                    break;
                }
            }
            if (trackIndex < 0 || format == null) {
                throw new RuntimeException("В файле не найдена аудиодорожка");
            }

            extractor.selectTrack(trackIndex);
            String mime = format.getString(MediaFormat.KEY_MIME);
            int sampleRate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE);
            int channels = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
            long durationUs = format.containsKey(MediaFormat.KEY_DURATION) ? format.getLong(MediaFormat.KEY_DURATION) : 0L;
            if (durationUs > 0) durationSec = durationUs / 1_000_000.0;

            codec = MediaCodec.createDecoderByType(mime);
            codec.configure(format, null, null, 0);
            codec.start();

            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            boolean sawInputEOS = false;
            boolean sawOutputEOS = false;
            long maxPtsUs = 0;

            while (!sawOutputEOS) {
                if (!sawInputEOS) {
                    int inIdx = codec.dequeueInputBuffer(10000);
                    if (inIdx >= 0) {
                        ByteBuffer inBuf = codec.getInputBuffer(inIdx);
                        int sampleSize = extractor.readSampleData(inBuf, 0);
                        if (sampleSize < 0) {
                            codec.queueInputBuffer(inIdx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            sawInputEOS = true;
                        } else {
                            long pts = extractor.getSampleTime();
                            if (pts > maxPtsUs) maxPtsUs = pts;
                            codec.queueInputBuffer(inIdx, 0, sampleSize, pts, 0);
                            extractor.advance();
                        }
                    }
                }

                int outIdx = codec.dequeueOutputBuffer(info, 10000);
                if (outIdx >= 0) {
                    if (info.size > 0) {
                        ByteBuffer outBuf = codec.getOutputBuffer(outIdx);
                        outBuf.position(info.offset);
                        outBuf.limit(info.offset + info.size);
                        ByteBuffer le = outBuf.slice().order(ByteOrder.LITTLE_ENDIAN);
                        int totalShorts = info.size / 2;
                        int frames = totalShorts / Math.max(1, channels);
                        short[] mono = new short[frames];
                        if (channels == 1) {
                            for (int i = 0; i < frames; i++) mono[i] = le.getShort();
                        } else {
                            for (int i = 0; i < frames; i++) {
                                int sum = 0;
                                for (int c = 0; c < channels; c++) sum += le.getShort();
                                mono[i] = (short) (sum / channels);
                            }
                        }
                        feedResampled16k(mono, frames, sampleRate, recognizer, allWords);
                    }

                    if (durationUs > 0 && info.presentationTimeUs > 0) {
                        int pct = (int) Math.min(99, (info.presentationTimeUs * 100L) / durationUs);
                        progressPercent = pct;
                        progressText = "Распознаю речь: " + pct + "%";
                    }

                    codec.releaseOutputBuffer(outIdx, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        sawOutputEOS = true;
                    }
                } else if (outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat outFmt = codec.getOutputFormat();
                    if (outFmt.containsKey(MediaFormat.KEY_SAMPLE_RATE)) {
                        sampleRate = outFmt.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                    }
                    if (outFmt.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) {
                        channels = outFmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                    }
                }
            }
            if (durationSec <= 0.0 && maxPtsUs > 0) {
                durationSec = maxPtsUs / 1_000_000.0;
            }
        } finally {
            if (codec != null) {
                try { codec.stop(); } catch (Exception ignored) {}
                try { codec.release(); } catch (Exception ignored) {}
            }
            extractor.release();
        }
        return durationSec;
    }

    private static void feedResampled16k(short[] mono, int len, int srcRate, Recognizer recognizer, JSONArray allWords) {
        if (len <= 0) return;
        byte[] pcmBytes;
        if (srcRate == 16000) {
            pcmBytes = new byte[len * 2];
            ByteBuffer.wrap(pcmBytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().put(mono, 0, len);
        } else {
            int outLen = Math.max(1, (int) Math.round(len * (16000.0 / srcRate)));
            pcmBytes = new byte[outLen * 2];
            ByteBuffer bb = ByteBuffer.wrap(pcmBytes).order(ByteOrder.LITTLE_ENDIAN);
            double ratio = (double) srcRate / 16000.0;
            for (int i = 0; i < outLen; i++) {
                double srcPos = i * ratio;
                int idx = (int) srcPos;
                if (idx >= len - 1) {
                    bb.putShort(mono[len - 1]);
                } else {
                    double frac = srcPos - idx;
                    short s = (short) (mono[idx] + frac * (mono[idx + 1] - mono[idx]));
                    bb.putShort(s);
                }
            }
        }

        if (recognizer.acceptWaveForm(pcmBytes, pcmBytes.length)) {
            appendWordsFromJson(recognizer.getResult(), allWords);
        }
    }
}
