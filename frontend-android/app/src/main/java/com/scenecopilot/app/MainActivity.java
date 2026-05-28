package com.scenecopilot.app;

import android.Manifest;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognizerIntent;
import android.speech.tts.TextToSpeech;
import android.view.MotionEvent;
import android.view.View;
import android.view.Surface;
import android.webkit.MimeTypeMap;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageCapture;
import androidx.camera.core.ImageCaptureException;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.core.content.ContextCompat;
import androidx.recyclerview.widget.LinearLayoutManager;

import com.google.common.util.concurrent.ListenableFuture;
import com.scenecopilot.app.databinding.ActivityMainBinding;
import com.scenecopilot.app.models.AcceptedResponse;
import com.scenecopilot.app.models.AudioChunkUploadResponse;
import com.scenecopilot.app.models.ChatRequest;
import com.scenecopilot.app.models.DocumentItem;
import com.scenecopilot.app.models.DocumentSearchResponse;
import com.scenecopilot.app.models.ReasoningEvent;
import com.scenecopilot.app.models.RunApprovalRequest;
import com.scenecopilot.app.models.RunApprovalResponse;
import com.scenecopilot.app.models.RunDetailResponse;
import com.scenecopilot.app.network.ApiClient;
import com.scenecopilot.app.network.EventStreamManager;
import com.scenecopilot.app.network.SceneCopilotService;
import com.scenecopilot.app.ui.EventAdapter;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import okhttp3.MediaType;
import okhttp3.MultipartBody;
import okhttp3.RequestBody;
import retrofit2.Call;
import retrofit2.Callback;
import retrofit2.Response;

public class MainActivity extends AppCompatActivity implements TextToSpeech.OnInitListener {
    private static final long LIVE_CAPTURE_INTERVAL_BASE_MS = 1800L;
    private static final long LIVE_CAPTURE_INTERVAL_MIN_MS = 1200L;
    private static final long LIVE_CAPTURE_INTERVAL_MAX_MS = 4200L;
    private static final long LIVE_CAPTURE_INTERVAL_STEP_MS = 300L;
    private static final long LIVE_CAPTURE_HEARTBEAT_MS = 6500L;
    private static final long LIVE_CAPTURE_TIMEOUT_MS = 9000L;
    private static final long AUDIO_THREAD_JOIN_TIMEOUT_MS = 1500L;
    private static final int LIVE_FRAME_HASH_GRID = 8;
    private static final int LIVE_FRAME_HASH_DIFF_THRESHOLD = 8;
    private static final int AUDIO_UPLOAD_CHUNK_BYTES = 32 * 1024;
    private static final int AUDIO_CAPTURE_READ_BYTES = 4096;
    private static final int AUDIO_SAMPLE_RATE_HZ = 16000;
    private static final int AUDIO_CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO;
    private static final int AUDIO_ENCODING = AudioFormat.ENCODING_PCM_16BIT;
    private static final int AUDIO_VAD_RMS_THRESHOLD = 950;
    private static final int AUDIO_VAD_HANGOVER_FRAMES = 6;
    private static final int AUDIO_VAD_PRE_ROLL_FRAMES = 2;

    private ActivityMainBinding binding;
    private SceneCopilotService service;
    private EventStreamManager streamManager;
    private EventAdapter eventAdapter;
    private Uri selectedImageUri;
    private byte[] capturedImageBytes;
    private TextToSpeech textToSpeech;
    private String currentSessionId;
    private String currentRunId;
    private String liveSessionId;
    private ProcessCameraProvider cameraProvider;
    private ImageCapture imageCapture;
    private ExecutorService cameraExecutor;
    private final Handler liveCaptureHandler = new Handler(Looper.getMainLooper());
    private boolean liveModeEnabled;
    private boolean liveCaptureInFlight;
    private int liveSubmittedFrames;
    private int liveDroppedFrames;
    private int liveSuppressedFrames;
    private int liveTimedOutFrames;
    private long liveCaptureIntervalMs = LIVE_CAPTURE_INTERVAL_BASE_MS;
    private long liveCaptureStartedAtMs;
    private long liveCaptureDeadlineAtMs;
    private long lastLiveSubmittedAtMs;
    private long lastSubmittedFrameHash;
    private long pendingLiveFrameHash;
    private boolean hasSubmittedFrameHash;
    private AudioRecord audioRecorder;
    private File pendingAudioFile;
    private boolean audioRecordingActive;
    private boolean audioRecordingStopRequested;
    private boolean audioCaptureCompleted;
    private boolean audioUploadInProgress;
    private boolean audioUploadFailed;
    private boolean audioUploadCancelled;
    private long audioRecordedBytes;
    private long audioUploadedBytes;
    private int audioNextChunkIndex;
    private long activeAudioWindowStartedAtMs;
    private long activeAudioWindowEndedAtMs;
    private String activeAudioUploadId;
    private String activeAudioPrompt;
    private String activeAudioSessionId;
    private boolean pendingPushToTalkPermissionRequest;
    private boolean audioPushToTalkMode;
    private boolean audioSpeechDetected;
    private Thread audioCaptureThread;
    private Thread audioUploadThread;
    private final Object audioStreamLock = new Object();

    private final Runnable liveCaptureRunnable = new Runnable() {
        @Override
        public void run() {
            if (!liveModeEnabled) {
                return;
            }
            if (imageCapture == null) {
                liveCaptureHandler.postDelayed(this, liveCaptureIntervalMs);
                return;
            }
            if (liveCaptureInFlight) {
                if (System.currentTimeMillis() >= liveCaptureDeadlineAtMs) {
                    recoverLiveCaptureTimeout();
                    liveCaptureHandler.postDelayed(this, liveCaptureIntervalMs);
                    return;
                }
                liveDroppedFrames += 1;
                increaseLiveCadencePressure(false);
                updateLiveStatsLabel();
            } else {
                captureAndAnalyzeLiveFrame();
            }
            liveCaptureHandler.postDelayed(this, liveCaptureIntervalMs);
        }
    };

    private final ActivityResultLauncher<String> imagePicker =
            registerForActivityResult(new ActivityResultContracts.GetContent(), uri -> {
                if (uri == null) {
                    return;
                }
                selectedImageUri = uri;
                capturedImageBytes = null;
                binding.previewImage.setImageURI(uri);
                binding.selectedFileLabel.setText(uri.toString());
            });

    private final ActivityResultLauncher<Void> cameraCapture =
            registerForActivityResult(new ActivityResultContracts.TakePicturePreview(), bitmap -> {
                if (bitmap == null) {
                    return;
                }
                selectedImageUri = null;
                capturedImageBytes = bitmapToJpeg(bitmap);
                binding.previewImage.setImageBitmap(bitmap);
                binding.selectedFileLabel.setText(getString(R.string.captured_photo_label));
            });

    private final ActivityResultLauncher<Intent> voicePromptLauncher =
            registerForActivityResult(new ActivityResultContracts.StartActivityForResult(), result -> {
                if (result.getResultCode() != RESULT_OK || result.getData() == null) {
                    binding.statusText.setText(R.string.voice_prompt_empty);
                    return;
                }
                ArrayList<String> matches = result.getData()
                        .getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
                if (matches == null || matches.isEmpty()) {
                    binding.statusText.setText(R.string.voice_prompt_empty);
                    return;
                }
                String spokenPrompt = matches.get(0);
                binding.promptInput.setText(spokenPrompt);
                binding.statusText.setText(R.string.status_voice_prompt_ready);
            });

    private final ActivityResultLauncher<String> cameraPermissionLauncher =
            registerForActivityResult(new ActivityResultContracts.RequestPermission(), granted -> {
                if (granted) {
                    startLiveModeInternal();
                    return;
                }
                binding.statusText.setText(R.string.status_live_permission_denied);
            });

    private final ActivityResultLauncher<String> audioPermissionLauncher =
            registerForActivityResult(new ActivityResultContracts.RequestPermission(), granted -> {
                if (granted) {
                    startAudioRecording(pendingPushToTalkPermissionRequest);
                    pendingPushToTalkPermissionRequest = false;
                    return;
                }
                pendingPushToTalkPermissionRequest = false;
                binding.statusText.setText(R.string.status_audio_permission_denied);
            });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        binding = ActivityMainBinding.inflate(getLayoutInflater());
        setContentView(binding.getRoot());

        service = ApiClient.service();
        streamManager = new EventStreamManager(ApiClient.okHttpClient(), ApiClient.baseUrl());
        eventAdapter = new EventAdapter();
        textToSpeech = new TextToSpeech(this, this);
        cameraExecutor = Executors.newSingleThreadExecutor();

        binding.eventsRecycler.setLayoutManager(new LinearLayoutManager(this));
        binding.eventsRecycler.setAdapter(eventAdapter);

        binding.topAppBar.setSubtitle(getString(R.string.backend_url, ApiClient.baseUrl()));
        binding.promptInput.setText(getString(R.string.default_prompt));
        syncPreviewSurface();
        updateLiveScanButton();

        binding.pickImageButton.setOnClickListener(v -> {
            if (liveModeEnabled) {
                stopLiveMode(getString(R.string.status_live_paused_manual));
            }
            imagePicker.launch("image/*");
        });
        binding.capturePhotoButton.setOnClickListener(v -> {
            if (liveModeEnabled) {
                stopLiveMode(getString(R.string.status_live_paused_manual));
            }
            cameraCapture.launch(null);
        });
        binding.liveScanButton.setOnClickListener(v -> toggleLiveMode());
        binding.quickReadButton.setOnClickListener(v ->
                binding.promptInput.setText(getString(R.string.quick_read_prompt)));
        binding.voicePromptButton.setOnClickListener(v -> launchVoicePrompt());
        binding.recordAudioButton.setOnClickListener(v -> toggleAudioRecording());
        binding.recordAudioButton.setOnLongClickListener(v -> {
            requestAudioRecordingStart(true);
            return true;
        });
        binding.recordAudioButton.setOnTouchListener((v, event) -> handleAudioRecordButtonTouch(event));
        binding.analyzeButton.setOnClickListener(v -> submitCurrentRequest());
        binding.searchDocsButton.setOnClickListener(v -> searchDocuments());
        binding.refreshRunButton.setOnClickListener(v -> {
            if (currentRunId == null || currentRunId.isEmpty()) {
                showError("No run to refresh yet.");
                return;
            }
            fetchRunDetail(currentRunId);
        });
        binding.approveRunButton.setOnClickListener(v -> resolveApproval("approve"));
        binding.rejectRunButton.setOnClickListener(v -> resolveApproval("reject"));
    }

    @Override
    protected void onDestroy() {
        cancelAudioRecording();
        stopLiveLoop();
        if (cameraProvider != null) {
            cameraProvider.unbindAll();
        }
        if (cameraExecutor != null) {
            cameraExecutor.shutdown();
        }
        streamManager.close();
        if (textToSpeech != null) {
            textToSpeech.stop();
            textToSpeech.shutdown();
        }
        super.onDestroy();
    }

    @Override
    protected void onStop() {
        if (liveModeEnabled) {
            stopLiveMode(getString(R.string.status_live_paused));
        }
        if (audioRecordingActive || audioUploadInProgress || isAudioUploadThreadBusy()) {
            cancelAudioRecording();
        }
        super.onStop();
    }

    @Override
    public void onInit(int status) {
        if (status == TextToSpeech.SUCCESS) {
            textToSpeech.setLanguage(Locale.US);
        }
    }

    private void submitCurrentRequest() {
        if (liveModeEnabled) {
            stopLiveMode(getString(R.string.status_live_paused_manual));
        }
        if (audioRecordingActive) {
            cancelAudioRecording();
        }
        String prompt = binding.promptInput.getText() != null
                ? binding.promptInput.getText().toString().trim()
                : "";
        if (prompt.isEmpty()) {
            prompt = getString(R.string.default_prompt);
        }

        eventAdapter.clear();
        binding.summaryText.setText(R.string.waiting_summary);
        binding.statusText.setText(R.string.status_uploading);
        binding.runMetaText.setText(R.string.run_detail_placeholder);
        binding.approvalSummaryText.setText(R.string.approval_summary_placeholder);
        binding.auditSummaryText.setText(R.string.audit_summary_placeholder);
        setApprovalControlsVisible(false);

        if (capturedImageBytes != null) {
            uploadImageBytes(prompt, capturedImageBytes, "camera_capture.jpg", "image/jpeg");
        } else if (selectedImageUri != null) {
            uploadImage(prompt);
        } else {
            postTextChat(prompt);
        }
    }

    private void toggleAudioRecording() {
        if (audioRecordingActive) {
            stopAudioRecordingAndUpload();
            return;
        }
        if (audioPushToTalkMode) {
            return;
        }
        requestAudioRecordingStart(false);
    }

    private void requestAudioRecordingStart(boolean pushToTalkMode) {
        if (audioRecordingActive) {
            return;
        }
        if (audioUploadInProgress || isAudioUploadThreadBusy()) {
            binding.statusText.setText(R.string.status_audio_stream_busy);
            return;
        }
        if (liveModeEnabled) {
            stopLiveMode(getString(R.string.status_live_paused_manual));
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED) {
            startAudioRecording(pushToTalkMode);
            return;
        }
        pendingPushToTalkPermissionRequest = pushToTalkMode;
        audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO);
    }

    private boolean handleAudioRecordButtonTouch(MotionEvent event) {
        if (!audioPushToTalkMode) {
            return false;
        }
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
            stopAudioRecordingAndUpload();
            return true;
        }
        return false;
    }

    private void startAudioRecording(boolean pushToTalkMode) {
        if (audioUploadInProgress || isAudioUploadThreadBusy()) {
            binding.statusText.setText(R.string.status_audio_stream_busy);
            return;
        }
        int minBufferSize = AudioRecord.getMinBufferSize(
                AUDIO_SAMPLE_RATE_HZ,
                AUDIO_CHANNEL_CONFIG,
                AUDIO_ENCODING
        );
        if (minBufferSize <= 0) {
            showError(getString(R.string.status_audio_recording_failed));
            return;
        }
        int bufferSize = Math.max(minBufferSize * 2, AUDIO_CAPTURE_READ_BYTES * 4);
        pendingAudioFile = new File(getCacheDir(), "audio_clip_" + System.currentTimeMillis() + ".pcm");
        String prompt = currentAudioPrompt();
        String sessionId = currentSessionId != null && !currentSessionId.isEmpty()
                ? currentSessionId
                : UUID.randomUUID().toString().substring(0, 12);
        try {
            audioRecorder = new AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    AUDIO_SAMPLE_RATE_HZ,
                    AUDIO_CHANNEL_CONFIG,
                    AUDIO_ENCODING,
                    bufferSize
            );
            if (audioRecorder.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IllegalStateException("AudioRecord failed to initialize");
            }
            initializeAudioStreaming(prompt, sessionId);
            audioPushToTalkMode = pushToTalkMode;
            audioSpeechDetected = false;
            audioRecordingStopRequested = false;
            audioRecorder.startRecording();
            audioRecordingActive = true;
            startAudioCaptureThread(AUDIO_CAPTURE_READ_BYTES, pendingAudioFile);
            startAudioUploadThread(pendingAudioFile);
            binding.recordAudioButton.setText(pushToTalkMode
                    ? R.string.release_to_send
                    : R.string.stop_recording);
            binding.recordAudioButton.setEnabled(true);
            binding.statusText.setText(R.string.status_audio_waiting_speech);
            binding.selectedFileLabel.setText(R.string.audio_streaming_label);
        } catch (Exception exc) {
            cancelAudioRecording();
            showError(getString(R.string.status_audio_recording_failed));
        }
    }

    private void stopAudioRecordingAndUpload() {
        try {
            if (audioRecorder != null) {
                audioRecorder.stop();
            }
        } catch (RuntimeException ignored) {
        } finally {
            audioRecordingStopRequested = true;
            joinAudioCaptureThread();
            releaseAudioRecorder();
        }

        binding.recordAudioButton.setText(R.string.record_audio);
        if (pendingAudioFile == null || !pendingAudioFile.exists() || audioRecordedBytes <= 0L) {
            cancelPendingAudioUpload();
            binding.recordAudioButton.setEnabled(true);
            binding.statusText.setText(R.string.status_audio_no_speech);
            audioPushToTalkMode = false;
            return;
        }
        activeAudioWindowEndedAtMs = System.currentTimeMillis();
        binding.recordAudioButton.setEnabled(false);
        binding.statusText.setText(R.string.status_audio_stream_finalizing);
        synchronized (audioStreamLock) {
            audioCaptureCompleted = true;
            audioStreamLock.notifyAll();
        }
        audioPushToTalkMode = false;
    }

    private void cancelAudioRecording() {
        try {
            if (audioRecorder != null) {
                audioRecorder.stop();
            }
        } catch (RuntimeException ignored) {
        } finally {
            audioRecordingStopRequested = true;
            cancelPendingAudioUpload();
            joinAudioCaptureThread();
            releaseAudioRecorder();
        }
        joinAudioUploadThread();
        cleanupPendingAudioFile();
        binding.recordAudioButton.setText(R.string.record_audio);
        binding.recordAudioButton.setEnabled(true);
        audioPushToTalkMode = false;
    }

    private void releaseAudioRecorder() {
        audioRecordingActive = false;
        if (audioRecorder != null) {
            audioRecorder.release();
            audioRecorder = null;
        }
    }

    private void startAudioCaptureThread(int bufferSize, File audioFile) {
        audioCaptureThread = new Thread(() -> {
            byte[] buffer = new byte[bufferSize];
            byte[] preRollBuffer = new byte[bufferSize * AUDIO_VAD_PRE_ROLL_FRAMES];
            int preRollSize = 0;
            int speechHangoverFrames = 0;
            try (FileOutputStream outputStream = new FileOutputStream(audioFile, false)) {
                while (!audioRecordingStopRequested && audioRecorder != null) {
                    int read = audioRecorder.read(buffer, 0, buffer.length);
                    if (read > 0) {
                        boolean speechFrame = isSpeechFrame(buffer, read);
                        if (speechFrame) {
                            if (!audioSpeechDetected && preRollSize > 0) {
                                outputStream.write(preRollBuffer, 0, preRollSize);
                                noteAudioBytesCaptured(preRollSize);
                                preRollSize = 0;
                            }
                            markAudioSpeechDetected();
                            outputStream.write(buffer, 0, read);
                            noteAudioBytesCaptured(read);
                            speechHangoverFrames = AUDIO_VAD_HANGOVER_FRAMES;
                        } else if (audioSpeechDetected && speechHangoverFrames > 0) {
                            outputStream.write(buffer, 0, read);
                            noteAudioBytesCaptured(read);
                            speechHangoverFrames -= 1;
                        } else if (!audioSpeechDetected) {
                            preRollSize = appendPreRollFrame(preRollBuffer, preRollSize, buffer, read);
                        }
                    } else if (read < 0) {
                        throw new IOException("AudioRecord read failed with code " + read);
                    }
                }
                outputStream.flush();
            } catch (IOException exc) {
                failAudioStreaming("Audio capture failed: " + exc.getMessage(), true);
            } finally {
                synchronized (audioStreamLock) {
                    audioCaptureCompleted = true;
                    audioStreamLock.notifyAll();
                }
            }
        }, "scenecopilot-audio-capture");
        audioCaptureThread.start();
    }

    private void joinAudioCaptureThread() {
        if (audioCaptureThread == null) {
            return;
        }
        try {
            audioCaptureThread.join(AUDIO_THREAD_JOIN_TIMEOUT_MS);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        } finally {
            audioCaptureThread = null;
        }
    }

    private boolean isSpeechFrame(byte[] buffer, int read) {
        int sampleCount = read / 2;
        if (sampleCount <= 0) {
            return false;
        }
        long sumSquares = 0L;
        for (int index = 0; index + 1 < read; index += 2) {
            int low = buffer[index] & 0xFF;
            int high = buffer[index + 1];
            short sample = (short) (low | (high << 8));
            long value = sample;
            sumSquares += value * value;
        }
        double rms = Math.sqrt(sumSquares / (double) sampleCount);
        return rms >= AUDIO_VAD_RMS_THRESHOLD;
    }

    private int appendPreRollFrame(byte[] preRollBuffer, int currentSize, byte[] buffer, int read) {
        if (read >= preRollBuffer.length) {
            System.arraycopy(buffer, read - preRollBuffer.length, preRollBuffer, 0, preRollBuffer.length);
            return preRollBuffer.length;
        }
        int overflow = Math.max(0, currentSize + read - preRollBuffer.length);
        if (overflow > 0) {
            System.arraycopy(preRollBuffer, overflow, preRollBuffer, 0, currentSize - overflow);
            currentSize -= overflow;
        }
        System.arraycopy(buffer, 0, preRollBuffer, currentSize, read);
        return currentSize + read;
    }

    private void markAudioSpeechDetected() {
        if (audioSpeechDetected) {
            return;
        }
        audioSpeechDetected = true;
        runOnUiThread(() -> binding.statusText.setText(
                audioPushToTalkMode
                        ? R.string.status_audio_push_to_talk_active
                        : R.string.status_recording_audio
        ));
    }

    private void toggleLiveMode() {
        if (liveModeEnabled) {
            stopLiveMode(getString(R.string.status_live_paused));
            return;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED) {
            startLiveModeInternal();
            return;
        }
        cameraPermissionLauncher.launch(Manifest.permission.CAMERA);
    }

    private void startLiveModeInternal() {
        selectedImageUri = null;
        capturedImageBytes = null;
        binding.previewImage.setImageDrawable(null);
        liveModeEnabled = true;
        liveCaptureInFlight = false;
        liveSubmittedFrames = 0;
        liveDroppedFrames = 0;
        liveSuppressedFrames = 0;
        liveTimedOutFrames = 0;
        liveCaptureIntervalMs = LIVE_CAPTURE_INTERVAL_BASE_MS;
        liveCaptureStartedAtMs = 0L;
        liveCaptureDeadlineAtMs = 0L;
        lastLiveSubmittedAtMs = 0L;
        lastSubmittedFrameHash = 0L;
        pendingLiveFrameHash = 0L;
        hasSubmittedFrameHash = false;
        liveSessionId = currentSessionId != null && !currentSessionId.isEmpty()
                ? currentSessionId
                : UUID.randomUUID().toString().substring(0, 12);
        syncPreviewSurface();
        updateLiveScanButton();
        binding.selectedFileLabel.setText(R.string.live_camera_idle);
        binding.statusText.setText(R.string.status_live_camera_starting);
        startCameraPreview();
    }

    private void stopLiveMode(String statusMessage) {
        liveModeEnabled = false;
        liveCaptureInFlight = false;
        liveCaptureStartedAtMs = 0L;
        liveCaptureDeadlineAtMs = 0L;
        pendingLiveFrameHash = 0L;
        stopLiveLoop();
        if (cameraProvider != null) {
            cameraProvider.unbindAll();
        }
        imageCapture = null;
        syncPreviewSurface();
        updateLiveScanButton();
        if (selectedImageUri == null && capturedImageBytes == null) {
            binding.selectedFileLabel.setText(R.string.no_image_selected);
        }
        if (statusMessage != null && !statusMessage.isEmpty()) {
            binding.statusText.setText(statusMessage);
        }
    }

    private void startCameraPreview() {
        ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(this);
        future.addListener(() -> {
            try {
                cameraProvider = future.get();
                bindCameraUseCases();
                binding.statusText.setText(R.string.status_live_camera_ready);
                updateLiveStatsLabel();
                scheduleNextLiveCapture();
            } catch (Exception exc) {
                stopLiveMode(getString(R.string.status_live_error));
                showError("Camera preview failed: " + exc.getMessage());
            }
        }, ContextCompat.getMainExecutor(this));
    }

    private void bindCameraUseCases() {
        if (cameraProvider == null) {
            return;
        }
        Preview preview = new Preview.Builder().build();
        preview.setSurfaceProvider(binding.cameraPreviewView.getSurfaceProvider());

        int targetRotation = binding.cameraPreviewView.getDisplay() != null
                ? binding.cameraPreviewView.getDisplay().getRotation()
                : Surface.ROTATION_0;

        imageCapture = new ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                .setJpegQuality(82)
                .setTargetRotation(targetRotation)
                .build();

        cameraProvider.unbindAll();
        cameraProvider.bindToLifecycle(
                this,
                CameraSelector.DEFAULT_BACK_CAMERA,
                preview,
                imageCapture
        );
    }

    private void scheduleNextLiveCapture() {
        stopLiveLoop();
        liveCaptureHandler.postDelayed(liveCaptureRunnable, 600L);
    }

    private void stopLiveLoop() {
        liveCaptureHandler.removeCallbacks(liveCaptureRunnable);
    }

    private void captureAndAnalyzeLiveFrame() {
        if (!liveModeEnabled || imageCapture == null) {
            return;
        }
        String prompt = binding.promptInput.getText() != null
                ? binding.promptInput.getText().toString().trim()
                : "";
        if (prompt.isEmpty()) {
            prompt = getString(R.string.default_prompt);
        }
        String sessionId = liveSessionId != null && !liveSessionId.isEmpty()
                ? liveSessionId
                : UUID.randomUUID().toString().substring(0, 12);
        liveCaptureInFlight = true;
        liveCaptureStartedAtMs = System.currentTimeMillis();
        liveCaptureDeadlineAtMs = liveCaptureStartedAtMs + LIVE_CAPTURE_TIMEOUT_MS;
        binding.statusText.setText(R.string.status_live_capturing);
        File outputFile = new File(getCacheDir(), "live_frame_" + System.currentTimeMillis() + ".jpg");
        ImageCapture.OutputFileOptions outputOptions =
                new ImageCapture.OutputFileOptions.Builder(outputFile).build();
        imageCapture.takePicture(outputOptions, cameraExecutor, new ImageCapture.OnImageSavedCallback() {
            @Override
            public void onImageSaved(@NonNull ImageCapture.OutputFileResults outputFileResults) {
                try {
                    byte[] payload = Files.readAllBytes(outputFile.toPath());
                    LiveFrameDecision frameDecision = decideLiveFrameUpload(payload);
                    if (!frameDecision.shouldUpload) {
                        liveCaptureInFlight = false;
                        liveCaptureStartedAtMs = 0L;
                        liveCaptureDeadlineAtMs = 0L;
                        liveSuppressedFrames += 1;
                        runOnUiThread(() -> {
                            increaseLiveCadenceForStableScene();
                            updateLiveStatsLabel();
                            binding.statusText.setText(R.string.status_live_scene_stable);
                        });
                        return;
                    }
                    pendingLiveFrameHash = frameDecision.frameHash;
                    uploadImageBytes(
                            prompt,
                            payload,
                            "live_frame.jpg",
                            "image/jpeg",
                            sessionId,
                            true
                    );
                } catch (IOException exc) {
                    liveCaptureInFlight = false;
                    liveCaptureStartedAtMs = 0L;
                    liveCaptureDeadlineAtMs = 0L;
                    runOnUiThread(() -> binding.statusText.setText(R.string.status_live_error));
                } finally {
                    if (outputFile.exists()) {
                        outputFile.delete();
                    }
                }
            }

            @Override
            public void onError(@NonNull ImageCaptureException exception) {
                liveCaptureInFlight = false;
                liveCaptureStartedAtMs = 0L;
                liveCaptureDeadlineAtMs = 0L;
                runOnUiThread(() -> binding.statusText.setText(R.string.status_live_error));
            }
        });
    }

    private void syncPreviewSurface() {
        binding.cameraPreviewView.setVisibility(liveModeEnabled ? View.VISIBLE : View.GONE);
        binding.previewImage.setVisibility(liveModeEnabled ? View.GONE : View.VISIBLE);
    }

    private void updateLiveScanButton() {
        binding.liveScanButton.setText(liveModeEnabled
                ? R.string.pause_live_scan
                : R.string.start_live_scan);
    }

    private void updateLiveStatsLabel() {
        binding.selectedFileLabel.setText(getString(
                R.string.live_frame_stats_extended,
                liveSubmittedFrames,
                liveDroppedFrames,
                liveTimedOutFrames,
                liveSuppressedFrames,
                liveCaptureIntervalMs / 1000.0
        ));
    }

    private void increaseLiveCadencePressure(boolean timeoutTriggered) {
        long previous = liveCaptureIntervalMs;
        liveCaptureIntervalMs = Math.min(
                LIVE_CAPTURE_INTERVAL_MAX_MS,
                liveCaptureIntervalMs + LIVE_CAPTURE_INTERVAL_STEP_MS
        );
        if (liveModeEnabled && liveCaptureIntervalMs > previous) {
            binding.statusText.setText(timeoutTriggered
                    ? R.string.status_live_timeout_recovered
                    : R.string.status_live_backpressure);
        }
    }

    private void relaxLiveCadence() {
        long previous = liveCaptureIntervalMs;
        liveCaptureIntervalMs = Math.max(
                LIVE_CAPTURE_INTERVAL_MIN_MS,
                liveCaptureIntervalMs - LIVE_CAPTURE_INTERVAL_STEP_MS
        );
        if (liveModeEnabled && liveCaptureIntervalMs < previous) {
            binding.statusText.setText(R.string.status_live_speeding_up);
        }
    }

    private void increaseLiveCadenceForStableScene() {
        long previous = liveCaptureIntervalMs;
        liveCaptureIntervalMs = Math.min(
                LIVE_CAPTURE_INTERVAL_MAX_MS,
                liveCaptureIntervalMs + LIVE_CAPTURE_INTERVAL_STEP_MS
        );
        if (liveModeEnabled && liveCaptureIntervalMs > previous) {
            binding.statusText.setText(R.string.status_live_scene_stable);
        }
    }

    private LiveFrameDecision decideLiveFrameUpload(byte[] payload) {
        long frameHash = computeFrameHash(payload);
        long nowMs = System.currentTimeMillis();
        boolean heartbeatDue = lastLiveSubmittedAtMs <= 0L
                || (nowMs - lastLiveSubmittedAtMs) >= LIVE_CAPTURE_HEARTBEAT_MS;
        if (!hasSubmittedFrameHash || heartbeatDue) {
            return new LiveFrameDecision(true, frameHash);
        }
        int diff = Long.bitCount(frameHash ^ lastSubmittedFrameHash);
        boolean changedEnough = diff >= LIVE_FRAME_HASH_DIFF_THRESHOLD;
        return new LiveFrameDecision(changedEnough, frameHash);
    }

    private long computeFrameHash(byte[] payload) {
        Bitmap decoded = BitmapFactory.decodeByteArray(payload, 0, payload.length);
        if (decoded == null) {
            return System.nanoTime();
        }
        Bitmap scaled = Bitmap.createScaledBitmap(decoded, LIVE_FRAME_HASH_GRID, LIVE_FRAME_HASH_GRID, true);
        if (scaled != decoded) {
            decoded.recycle();
        }
        int width = scaled.getWidth();
        int height = scaled.getHeight();
        long sum = 0L;
        int[] luminance = new int[width * height];
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                int pixel = scaled.getPixel(x, y);
                int red = (pixel >> 16) & 0xFF;
                int green = (pixel >> 8) & 0xFF;
                int blue = pixel & 0xFF;
                int gray = (red * 30 + green * 59 + blue * 11) / 100;
                int index = y * width + x;
                luminance[index] = gray;
                sum += gray;
            }
        }
        scaled.recycle();
        int average = (int) (sum / Math.max(1, luminance.length));
        long hash = 0L;
        for (int gray : luminance) {
            hash <<= 1;
            if (gray >= average) {
                hash |= 1L;
            }
        }
        return hash;
    }

    private void recoverLiveCaptureTimeout() {
        liveCaptureInFlight = false;
        liveCaptureStartedAtMs = 0L;
        liveCaptureDeadlineAtMs = 0L;
        liveTimedOutFrames += 1;
        increaseLiveCadencePressure(true);
        updateLiveStatsLabel();
        if (currentRunId != null && !currentRunId.isEmpty()) {
            fetchRunDetail(currentRunId, true);
        }
    }

    private void launchVoicePrompt() {
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault());
        intent.putExtra(RecognizerIntent.EXTRA_PROMPT, getString(R.string.voice_prompt_hint));
        try {
            binding.statusText.setText(R.string.status_listening);
            voicePromptLauncher.launch(intent);
        } catch (ActivityNotFoundException ex) {
            showError(getString(R.string.voice_prompt_missing));
        }
    }

    private void postTextChat(String prompt) {
        currentSessionId = UUID.randomUUID().toString().substring(0, 12);
        service.chat(new ChatRequest(prompt, currentSessionId)).enqueue(new Callback<AcceptedResponse>() {
            @Override
            public void onResponse(@NonNull Call<AcceptedResponse> call, @NonNull Response<AcceptedResponse> response) {
                if (!response.isSuccessful() || response.body() == null) {
                    showError("Chat request failed: " + response.code());
                    return;
                }
                binding.statusText.setText(getString(R.string.status_queued, response.body().queuePosition));
                startEventStream(response.body().sessionId, response.body().runId);
            }

            @Override
            public void onFailure(@NonNull Call<AcceptedResponse> call, @NonNull Throwable t) {
                showError("Chat request failed: " + t.getMessage());
            }
        });
    }

    private void uploadImage(String prompt) {
        try {
            MultipartBody.Part imagePart = buildImagePart(selectedImageUri);
            RequestBody promptBody = RequestBody.create(prompt, MediaType.parse("text/plain"));
            currentSessionId = currentOrNewSessionId();
            RequestBody sessionBody = RequestBody.create(currentSessionId, MediaType.parse("text/plain"));
            RequestBody capturedAtBody = RequestBody.create(
                    String.valueOf(System.currentTimeMillis()),
                    MediaType.parse("text/plain")
            );

            service.analyzeScene(imagePart, promptBody, sessionBody, capturedAtBody).enqueue(new Callback<AcceptedResponse>() {
                @Override
                public void onResponse(@NonNull Call<AcceptedResponse> call, @NonNull Response<AcceptedResponse> response) {
                    if (!response.isSuccessful() || response.body() == null) {
                        showError("Image analyze failed: " + response.code());
                        return;
                    }
                    binding.statusText.setText(getString(R.string.status_queued, response.body().queuePosition));
                    startEventStream(response.body().sessionId, response.body().runId);
                }

                @Override
                public void onFailure(@NonNull Call<AcceptedResponse> call, @NonNull Throwable t) {
                    showError("Image analyze failed: " + t.getMessage());
                }
            });
        } catch (IOException e) {
            showError("Could not read selected image: " + e.getMessage());
        }
    }

    private void initializeAudioStreaming(String prompt, String sessionId) {
        currentSessionId = sessionId;
        synchronized (audioStreamLock) {
            activeAudioWindowStartedAtMs = System.currentTimeMillis();
            activeAudioWindowEndedAtMs = 0L;
            activeAudioUploadId = UUID.randomUUID().toString().substring(0, 12);
            activeAudioPrompt = prompt;
            activeAudioSessionId = sessionId;
            audioUploadInProgress = true;
            audioUploadFailed = false;
            audioUploadCancelled = false;
            audioCaptureCompleted = false;
            audioRecordedBytes = 0L;
            audioUploadedBytes = 0L;
            audioNextChunkIndex = 0;
            audioStreamLock.notifyAll();
        }
    }

    private String currentAudioPrompt() {
        String prompt = binding.promptInput.getText() != null
                ? binding.promptInput.getText().toString().trim()
                : "";
        if (prompt.isEmpty()) {
            prompt = getString(R.string.default_audio_prompt);
        }
        return prompt;
    }

    private void startAudioUploadThread(File audioFile) {
        audioUploadThread = new Thread(() -> {
            try {
                while (true) {
                    AudioChunkPlan plan = awaitNextAudioChunkPlan();
                    if (plan == null) {
                        return;
                    }
                    runOnUiThread(() -> binding.statusText.setText(plan.finalChunk
                            ? getString(R.string.status_audio_stream_finalizing)
                            : getString(R.string.status_audio_chunk_uploading, plan.chunkIndex + 1)));
                    byte[] payload = readAudioChunk(audioFile, plan.offset, plan.size);
                    AudioChunkUploadResponse body = uploadAudioChunkSync(payload, plan);
                    finishAudioChunkUpload(plan, body);
                    if (plan.finalChunk) {
                        return;
                    }
                }
            } catch (IOException exc) {
                failAudioStreaming("Audio chunk upload failed: " + exc.getMessage(), true);
            } finally {
                synchronized (audioStreamLock) {
                    audioUploadInProgress = false;
                    audioStreamLock.notifyAll();
                }
            }
        }, "scenecopilot-audio-upload");
        audioUploadThread.start();
    }

    private AudioChunkPlan awaitNextAudioChunkPlan() {
        synchronized (audioStreamLock) {
            while (true) {
                if (audioUploadCancelled || audioUploadFailed) {
                    return null;
                }
                long availableBytes = audioRecordedBytes - audioUploadedBytes;
                if (availableBytes >= AUDIO_UPLOAD_CHUNK_BYTES) {
                    return new AudioChunkPlan(
                            audioUploadedBytes,
                            AUDIO_UPLOAD_CHUNK_BYTES,
                            audioNextChunkIndex,
                            false
                    );
                }
                if (audioCaptureCompleted) {
                    if (audioRecordedBytes <= 0L && audioNextChunkIndex == 0) {
                        return null;
                    }
                    return new AudioChunkPlan(
                            audioUploadedBytes,
                            (int) Math.max(0L, availableBytes),
                            audioNextChunkIndex,
                            true
                    );
                }
                try {
                    audioStreamLock.wait(250L);
                } catch (InterruptedException exc) {
                    Thread.currentThread().interrupt();
                    return null;
                }
            }
        }
    }

    private AudioChunkUploadResponse uploadAudioChunkSync(byte[] payload, AudioChunkPlan plan) throws IOException {
        MultipartBody.Part audioPart = buildAudioChunkPart(payload, plan.chunkIndex, plan.finalChunk);
        RequestBody promptBody = RequestBody.create(activeAudioPrompt, MediaType.parse("text/plain"));
        RequestBody sessionBody = RequestBody.create(activeAudioSessionId, MediaType.parse("text/plain"));
        RequestBody uploadIdBody = RequestBody.create(activeAudioUploadId, MediaType.parse("text/plain"));
        RequestBody chunkIndexBody = RequestBody.create(String.valueOf(plan.chunkIndex), MediaType.parse("text/plain"));
        RequestBody finalChunkBody = RequestBody.create(String.valueOf(plan.finalChunk), MediaType.parse("text/plain"));
        RequestBody audioExtBody = RequestBody.create(".wav", MediaType.parse("text/plain"));
        RequestBody audioFormatBody = RequestBody.create("pcm16le_mono_16000", MediaType.parse("text/plain"));
        long windowEndMs = plan.finalChunk && activeAudioWindowEndedAtMs > 0L
                ? activeAudioWindowEndedAtMs
                : System.currentTimeMillis();
        RequestBody windowStartedBody = RequestBody.create(
                String.valueOf(activeAudioWindowStartedAtMs),
                MediaType.parse("text/plain")
        );
        RequestBody windowEndedBody = RequestBody.create(
                String.valueOf(windowEndMs),
                MediaType.parse("text/plain")
        );

        Response<AudioChunkUploadResponse> response = service.uploadAudioChunk(
                audioPart,
                promptBody,
                sessionBody,
                uploadIdBody,
                chunkIndexBody,
                finalChunkBody,
                audioExtBody,
                audioFormatBody,
                windowStartedBody,
                windowEndedBody
        ).execute();
        if (!response.isSuccessful() || response.body() == null) {
            throw new IOException("HTTP " + response.code());
        }
        return response.body();
    }

    private void finishAudioChunkUpload(AudioChunkPlan plan, AudioChunkUploadResponse body) {
        synchronized (audioStreamLock) {
            audioUploadedBytes = plan.offset + plan.size;
            audioNextChunkIndex = plan.chunkIndex + 1;
            if (body.sessionId != null && !body.sessionId.isEmpty()) {
                activeAudioSessionId = body.sessionId;
            }
            if (plan.finalChunk) {
                audioUploadInProgress = false;
            }
            audioStreamLock.notifyAll();
        }

        String nextSessionId = body.sessionId != null && !body.sessionId.isEmpty()
                ? body.sessionId
                : activeAudioSessionId;
        currentSessionId = nextSessionId;

        if (!plan.finalChunk) {
            runOnUiThread(() -> binding.selectedFileLabel.setText(R.string.audio_streaming_label));
            return;
        }

        cleanupPendingAudioFile();
        runOnUiThread(() -> {
            binding.recordAudioButton.setEnabled(true);
            binding.recordAudioButton.setText(R.string.record_audio);
            binding.statusText.setText(getString(
                    R.string.status_audio_chunk_queued,
                    body.queuePosition != null ? body.queuePosition : 0
            ));
            binding.selectedFileLabel.setText(R.string.audio_streaming_label);
        });
        if (body.runId != null && !body.runId.isEmpty()) {
            startEventStream(nextSessionId, body.runId);
        }
    }

    private void noteAudioBytesCaptured(int bytes) {
        synchronized (audioStreamLock) {
            audioRecordedBytes += bytes;
            audioStreamLock.notifyAll();
        }
    }

    private byte[] readAudioChunk(File audioFile, long offset, int size) throws IOException {
        if (size < 0) {
            throw new IOException("Invalid audio chunk size " + size);
        }
        if (size == 0) {
            return new byte[0];
        }
        byte[] payload = new byte[size];
        try (RandomAccessFile raf = new RandomAccessFile(audioFile, "r")) {
            raf.seek(offset);
            raf.readFully(payload);
        }
        return payload;
    }

    private void cancelPendingAudioUpload() {
        synchronized (audioStreamLock) {
            audioUploadCancelled = true;
            audioUploadInProgress = false;
            audioCaptureCompleted = true;
            audioStreamLock.notifyAll();
        }
    }

    private void failAudioStreaming(String message, boolean cleanupFile) {
        synchronized (audioStreamLock) {
            audioUploadFailed = true;
            audioUploadInProgress = false;
            audioCaptureCompleted = true;
            audioStreamLock.notifyAll();
        }
        audioRecordingStopRequested = true;
        runOnUiThread(() -> {
            binding.recordAudioButton.setEnabled(true);
            binding.recordAudioButton.setText(R.string.record_audio);
            if (audioRecordingActive) {
                cancelAudioRecording();
            } else if (cleanupFile) {
                cleanupPendingAudioFile();
            }
            audioPushToTalkMode = false;
            showError(message);
        });
    }

    private void joinAudioUploadThread() {
        if (audioUploadThread == null) {
            return;
        }
        try {
            audioUploadThread.join(AUDIO_THREAD_JOIN_TIMEOUT_MS);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        } finally {
            if (!audioUploadThread.isAlive()) {
                audioUploadThread = null;
            }
        }
    }

    private boolean isAudioUploadThreadBusy() {
        return audioUploadThread != null && audioUploadThread.isAlive();
    }

    private MultipartBody.Part buildAudioChunkPart(byte[] bytes, int chunkIndex, boolean finalChunk) {
        String name = finalChunk
                ? String.format(Locale.US, "audio_chunk_%04d_final.part", chunkIndex)
                : String.format(Locale.US, "audio_chunk_%04d.part", chunkIndex);
        RequestBody body = RequestBody.create(bytes, MediaType.parse("application/octet-stream"));
        return MultipartBody.Part.createFormData("audio", name, body);
    }

    private void cleanupPendingAudioFile() {
        if (pendingAudioFile != null && pendingAudioFile.exists()) {
            pendingAudioFile.delete();
        }
        pendingAudioFile = null;
    }

    private String currentOrNewSessionId() {
        return currentSessionId != null && !currentSessionId.isEmpty()
                ? currentSessionId
                : UUID.randomUUID().toString().substring(0, 12);
    }

    private static final class LiveFrameDecision {
        private final boolean shouldUpload;
        private final long frameHash;

        private LiveFrameDecision(boolean shouldUpload, long frameHash) {
            this.shouldUpload = shouldUpload;
            this.frameHash = frameHash;
        }
    }

    private static final class AudioChunkPlan {
        private final long offset;
        private final int size;
        private final int chunkIndex;
        private final boolean finalChunk;

        private AudioChunkPlan(long offset, int size, int chunkIndex, boolean finalChunk) {
            this.offset = offset;
            this.size = size;
            this.chunkIndex = chunkIndex;
            this.finalChunk = finalChunk;
        }
    }

    private void uploadImageBytes(String prompt, byte[] bytes, String fileName, String mimeType) {
        uploadImageBytes(
                prompt,
                bytes,
                fileName,
                mimeType,
                currentOrNewSessionId(),
                false
        );
    }

    private void uploadImageBytes(
            String prompt,
            byte[] bytes,
            String fileName,
            String mimeType,
            String sessionId,
            boolean liveFrame
    ) {
        MultipartBody.Part imagePart = buildImagePart(bytes, fileName, mimeType);
        RequestBody promptBody = RequestBody.create(prompt, MediaType.parse("text/plain"));
        currentSessionId = sessionId;
        RequestBody sessionBody = RequestBody.create(sessionId, MediaType.parse("text/plain"));
        RequestBody capturedAtBody = RequestBody.create(
                String.valueOf(System.currentTimeMillis()),
                MediaType.parse("text/plain")
        );

        service.analyzeScene(imagePart, promptBody, sessionBody, capturedAtBody).enqueue(new Callback<AcceptedResponse>() {
            @Override
            public void onResponse(@NonNull Call<AcceptedResponse> call, @NonNull Response<AcceptedResponse> response) {
                if (!response.isSuccessful() || response.body() == null) {
                    liveCaptureInFlight = false;
                    liveCaptureStartedAtMs = 0L;
                    liveCaptureDeadlineAtMs = 0L;
                    if (liveFrame) {
                        binding.statusText.setText(R.string.status_live_error);
                        return;
                    }
                    showError("Camera analyze failed: " + response.code());
                    return;
                }
                currentSessionId = response.body().sessionId;
                currentRunId = response.body().runId;
                if (liveFrame) {
                    liveSessionId = response.body().sessionId;
                    hasSubmittedFrameHash = true;
                    lastSubmittedFrameHash = pendingLiveFrameHash;
                    lastLiveSubmittedAtMs = System.currentTimeMillis();
                    pendingLiveFrameHash = 0L;
                    liveSubmittedFrames += 1;
                    if (response.body().queuePosition > 0) {
                        increaseLiveCadencePressure(false);
                    } else {
                        relaxLiveCadence();
                    }
                    updateLiveStatsLabel();
                    binding.statusText.setText(getString(R.string.status_live_queued, response.body().queuePosition));
                } else {
                    binding.statusText.setText(getString(R.string.status_queued, response.body().queuePosition));
                }
                startEventStream(response.body().sessionId, response.body().runId);
            }

            @Override
            public void onFailure(@NonNull Call<AcceptedResponse> call, @NonNull Throwable t) {
                liveCaptureInFlight = false;
                liveCaptureStartedAtMs = 0L;
                liveCaptureDeadlineAtMs = 0L;
                pendingLiveFrameHash = 0L;
                if (liveFrame) {
                    binding.statusText.setText(R.string.status_live_error);
                    return;
                }
                showError("Camera analyze failed: " + t.getMessage());
            }
        });
    }

    private MultipartBody.Part buildImagePart(Uri uri) throws IOException {
        String mimeType = getContentResolver().getType(uri);
        if (mimeType == null) {
            mimeType = "image/jpeg";
        }
        String extension = MimeTypeMap.getSingleton().getExtensionFromMimeType(mimeType);
        if (extension == null || extension.isEmpty()) {
            extension = "jpg";
        }

        byte[] bytes = readUriBytes(uri);
        return buildImagePart(bytes, "scene_upload." + extension, mimeType);
    }

    private MultipartBody.Part buildImagePart(byte[] bytes, String fileName, String mimeType) {
        RequestBody body = RequestBody.create(bytes, MediaType.parse(mimeType));
        return MultipartBody.Part.createFormData("image", fileName, body);
    }

    private byte[] readUriBytes(Uri uri) throws IOException {
        try (InputStream inputStream = getContentResolver().openInputStream(uri);
             ByteArrayOutputStream outputStream = new ByteArrayOutputStream()) {
            if (inputStream == null) {
                throw new IOException("Input stream is null");
            }
            byte[] buffer = new byte[8192];
            int read;
            while ((read = inputStream.read(buffer)) != -1) {
                outputStream.write(buffer, 0, read);
            }
            return outputStream.toByteArray();
        }
    }

    private void startEventStream(String sessionId, String runId) {
        currentSessionId = sessionId;
        currentRunId = runId;
        streamManager.subscribe(sessionId, runId, new EventStreamManager.Listener() {
            @Override
            public void onOpen() {
                runOnUiThread(() -> binding.statusText.setText(getString(R.string.status_streaming, sessionId)));
            }

            @Override
            public void onEvent(ReasoningEvent event) {
                runOnUiThread(() -> {
                    if ("heartbeat".equals(event.eventType)) {
                        return;
                    }
                    eventAdapter.addEvent(event);
                    binding.eventsRecycler.smoothScrollToPosition(Math.max(0, eventAdapter.getItemCount() - 1));
                    if ("approval".equals(event.eventType)) {
                        binding.statusText.setText(R.string.status_approval_needed);
                        if (liveModeEnabled) {
                            liveCaptureInFlight = false;
                            liveCaptureStartedAtMs = 0L;
                            liveCaptureDeadlineAtMs = 0L;
                            stopLiveMode(getString(R.string.status_live_waiting_review));
                        }
                    }
                    if ("final".equals(event.eventType)) {
                        String message = event.getDisplayBody();
                        binding.summaryText.setText(message);
                        speak(message);
                        if (liveModeEnabled) {
                            liveCaptureInFlight = false;
                            liveCaptureStartedAtMs = 0L;
                            liveCaptureDeadlineAtMs = 0L;
                            relaxLiveCadence();
                            updateLiveStatsLabel();
                            binding.statusText.setText(getString(
                                    R.string.status_live_running,
                                    liveSubmittedFrames,
                                    liveDroppedFrames
                            ));
                        }
                    }
                    if ("error".equals(event.eventType) && liveModeEnabled) {
                        liveCaptureInFlight = false;
                        liveCaptureStartedAtMs = 0L;
                        liveCaptureDeadlineAtMs = 0L;
                        increaseLiveCadencePressure(false);
                        updateLiveStatsLabel();
                        binding.statusText.setText(R.string.status_live_error);
                    }
                    if ("final".equals(event.eventType)
                            || "approval".equals(event.eventType)
                            || "approval_resolved".equals(event.eventType)
                            || "run_started".equals(event.eventType)) {
                        fetchRunDetail(runId);
                    }
                });
            }

            @Override
            public void onFailure(String message) {
                runOnUiThread(() -> {
                    if (liveModeEnabled) {
                        liveCaptureInFlight = false;
                        liveCaptureStartedAtMs = 0L;
                        liveCaptureDeadlineAtMs = 0L;
                        increaseLiveCadencePressure(false);
                        updateLiveStatsLabel();
                        binding.statusText.setText(R.string.status_live_error);
                        return;
                    }
                    showError("SSE failed: " + message);
                });
            }
        });
    }

    private void fetchRunDetail(String runId) {
        fetchRunDetail(runId, false);
    }

    private void fetchRunDetail(String runId, boolean background) {
        if (!background) {
            binding.statusText.setText(R.string.status_loading_run_detail);
        }
        service.getRun(runId).enqueue(new Callback<RunDetailResponse>() {
            @Override
            public void onResponse(@NonNull Call<RunDetailResponse> call, @NonNull Response<RunDetailResponse> response) {
                if (!response.isSuccessful() || response.body() == null) {
                    if (!background) {
                        showError("Run detail failed: " + response.code());
                    }
                    return;
                }
                renderRunDetail(response.body());
            }

            @Override
            public void onFailure(@NonNull Call<RunDetailResponse> call, @NonNull Throwable t) {
                if (!background) {
                    showError("Run detail failed: " + t.getMessage());
                }
            }
        });
    }

    private void renderRunDetail(RunDetailResponse run) {
        currentRunId = run.id;
        String route = run.routeName != null ? run.routeName : "n/a";
        String stage = run.currentStage != null ? run.currentStage : "n/a";
        String latency = run.latencyMs != null ? String.format(Locale.US, "%.1f ms", run.latencyMs) : "n/a";
        binding.runMetaText.setText(
                "Run: " + run.id + "\n"
                        + "Status: " + stringValue(run.status) + "\n"
                        + "Route: " + route + "\n"
                        + "Stage: " + stage + "\n"
                        + "Latency: " + latency + "\n"
                        + "Artifacts: " + run.artifacts.size() + " · Action cards: " + run.actionCards.size()
        );

        binding.approvalSummaryText.setText(buildApprovalSummary(run));
        binding.auditSummaryText.setText(buildAuditSummary(run));
        if (run.outputText != null && !run.outputText.isEmpty()) {
            binding.summaryText.setText(run.outputText);
        }
        if (run.isTerminal()) {
            liveCaptureInFlight = false;
            liveCaptureStartedAtMs = 0L;
            liveCaptureDeadlineAtMs = 0L;
            if (liveModeEnabled) {
                if (run.latencyMs != null && run.latencyMs > 2500) {
                    increaseLiveCadencePressure(false);
                } else {
                    relaxLiveCadence();
                }
                updateLiveStatsLabel();
            }
        }
        binding.statusText.setText(run.isAwaitingApproval()
                ? getString(R.string.status_approval_needed)
                : liveModeEnabled
                ? getString(R.string.status_live_running, liveSubmittedFrames, liveDroppedFrames)
                : getString(R.string.status_done));
        setApprovalControlsVisible(run.isAwaitingApproval());
    }

    private String buildApprovalSummary(RunDetailResponse run) {
        if (run.approvals == null || run.approvals.isEmpty()) {
            return getString(R.string.approval_summary_placeholder);
        }
        Map<String, Object> latest = run.approvals.get(run.approvals.size() - 1);
        String status = stringValue(latest.get("status"));
        String risk = stringValue(latest.get("risk_level"));
        String reason = stringValue(latest.get("reason"));
        String note = stringValue(latest.get("reviewer_note"));
        StringBuilder builder = new StringBuilder();
        builder.append("Approval: ").append(status)
                .append(" · risk ").append(risk)
                .append("\nReason: ").append(reason);
        if (!note.isEmpty() && !"null".equalsIgnoreCase(note)) {
            builder.append("\nReviewer note: ").append(note);
        }
        if (run.artifacts != null && !run.artifacts.isEmpty()) {
            builder.append("\nLatest artifacts: ");
            for (int i = Math.max(0, run.artifacts.size() - 3); i < run.artifacts.size(); i++) {
                Map<String, Object> artifact = run.artifacts.get(i);
                if (i > Math.max(0, run.artifacts.size() - 3)) {
                    builder.append(" | ");
                }
                builder.append(stringValue(artifact.get("artifact_type")));
            }
        }
        return builder.toString();
    }

    private String buildAuditSummary(RunDetailResponse run) {
        if (run.auditLog == null || run.auditLog.isEmpty()) {
            return getString(R.string.audit_summary_placeholder);
        }
        StringBuilder builder = new StringBuilder();
        builder.append("Recent audit: ");
        int start = Math.max(0, run.auditLog.size() - 4);
        for (int i = start; i < run.auditLog.size(); i++) {
            Map<String, Object> item = run.auditLog.get(i);
            if (i > start) {
                builder.append(" -> ");
            }
            builder.append(stringValue(item.get("event_type")));
        }
        if (run.actionCards != null && !run.actionCards.isEmpty()) {
            Map<String, Object> latestCard = run.actionCards.get(run.actionCards.size() - 1);
            builder.append("\nAction card: ")
                    .append(stringValue(latestCard.get("title")))
                    .append(" (")
                    .append(stringValue(latestCard.get("status")))
                    .append(")");
        }
        return builder.toString();
    }

    private void resolveApproval(String decision) {
        if (currentRunId == null || currentRunId.isEmpty()) {
            showError("No run selected for approval.");
            return;
        }
        String note = binding.approvalNoteInput.getText() != null
                ? binding.approvalNoteInput.getText().toString().trim()
                : "";
        binding.statusText.setText(R.string.status_resolving_approval);
        service.resolveApproval(currentRunId, new RunApprovalRequest(decision, note.isEmpty() ? null : note))
                .enqueue(new Callback<RunApprovalResponse>() {
                    @Override
                    public void onResponse(@NonNull Call<RunApprovalResponse> call, @NonNull Response<RunApprovalResponse> response) {
                        if (!response.isSuccessful() || response.body() == null) {
                            showError("Approval update failed: " + response.code());
                            return;
                        }
                        binding.approvalNoteInput.setText("");
                        fetchRunDetail(currentRunId);
                    }

                    @Override
                    public void onFailure(@NonNull Call<RunApprovalResponse> call, @NonNull Throwable t) {
                        showError("Approval update failed: " + t.getMessage());
                    }
                });
    }

    private void searchDocuments() {
        String query = binding.docsQueryInput.getText() != null
                ? binding.docsQueryInput.getText().toString().trim()
                : "";
        if (query.isEmpty()) {
            showError("Enter a document query first.");
            return;
        }

        binding.documentResultsText.setText(R.string.status_searching_docs);
        service.searchDocuments(query, 5).enqueue(new Callback<DocumentSearchResponse>() {
            @Override
            public void onResponse(@NonNull Call<DocumentSearchResponse> call, @NonNull Response<DocumentSearchResponse> response) {
                if (!response.isSuccessful() || response.body() == null) {
                    showError("Document search failed: " + response.code());
                    return;
                }
                renderDocuments(response.body().items);
            }

            @Override
            public void onFailure(@NonNull Call<DocumentSearchResponse> call, @NonNull Throwable t) {
                showError("Document search failed: " + t.getMessage());
            }
        });
    }

    private void renderDocuments(List<DocumentItem> items) {
        if (items == null || items.isEmpty()) {
            binding.documentResultsText.setText(R.string.no_documents_found);
            return;
        }

        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < items.size(); i++) {
            DocumentItem item = items.get(i);
            builder.append(i + 1)
                    .append(". ")
                    .append(item.title)
                    .append("\n")
                    .append(item.snippet != null && !item.snippet.isEmpty() ? item.snippet : item.summary)
                    .append("\n")
                    .append(item.source != null ? item.source : "local")
                    .append(item.sourcePath != null && !item.sourcePath.isEmpty() ? " · " + item.sourcePath : "")
                    .append("\n\n");
        }
        binding.documentResultsText.setText(builder.toString().trim());
    }

    private void showError(String message) {
        binding.statusText.setText(message);
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }

    private byte[] bitmapToJpeg(Bitmap bitmap) {
        ByteArrayOutputStream outputStream = new ByteArrayOutputStream();
        bitmap.compress(Bitmap.CompressFormat.JPEG, 92, outputStream);
        return outputStream.toByteArray();
    }

    private void setApprovalControlsVisible(boolean visible) {
        int state = visible ? View.VISIBLE : View.GONE;
        binding.approvalNoteLayout.setVisibility(state);
        binding.approveRunButton.setVisibility(state);
        binding.rejectRunButton.setVisibility(state);
    }

    private String stringValue(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private void speak(String message) {
        if (textToSpeech != null) {
            textToSpeech.speak(message, TextToSpeech.QUEUE_FLUSH, null, "scenecopilot-final");
        }
    }
}
