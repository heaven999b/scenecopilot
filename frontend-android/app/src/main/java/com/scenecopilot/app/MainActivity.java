package com.scenecopilot.app;

import android.Manifest;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognizerIntent;
import android.speech.tts.TextToSpeech;
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
    private static final long LIVE_CAPTURE_TIMEOUT_MS = 9000L;
    private static final int AUDIO_UPLOAD_CHUNK_BYTES = 192 * 1024;

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
    private int liveTimedOutFrames;
    private long liveCaptureIntervalMs = LIVE_CAPTURE_INTERVAL_BASE_MS;
    private long liveCaptureStartedAtMs;
    private long liveCaptureDeadlineAtMs;
    private MediaRecorder audioRecorder;
    private File pendingAudioFile;
    private boolean audioRecordingActive;

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
                    startAudioRecording();
                    return;
                }
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
        if (audioRecordingActive) {
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
        if (liveModeEnabled) {
            stopLiveMode(getString(R.string.status_live_paused_manual));
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED) {
            startAudioRecording();
            return;
        }
        audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO);
    }

    private void startAudioRecording() {
        pendingAudioFile = new File(getCacheDir(), "audio_clip_" + System.currentTimeMillis() + ".m4a");
        audioRecorder = new MediaRecorder();
        try {
            audioRecorder.setAudioSource(MediaRecorder.AudioSource.MIC);
            audioRecorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
            audioRecorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            audioRecorder.setAudioSamplingRate(16000);
            audioRecorder.setAudioEncodingBitRate(64000);
            audioRecorder.setOutputFile(pendingAudioFile.getAbsolutePath());
            audioRecorder.prepare();
            audioRecorder.start();
            audioRecordingActive = true;
            binding.recordAudioButton.setText(R.string.stop_recording);
            binding.statusText.setText(R.string.status_recording_audio);
            binding.selectedFileLabel.setText(R.string.audio_recorded_label);
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
            releaseAudioRecorder();
        }

        binding.recordAudioButton.setText(R.string.record_audio);
        if (pendingAudioFile == null || !pendingAudioFile.exists()) {
            binding.statusText.setText(R.string.status_audio_recording_failed);
            return;
        }
        binding.statusText.setText(R.string.status_audio_uploading);

        String prompt = binding.promptInput.getText() != null
                ? binding.promptInput.getText().toString().trim()
                : "";
        if (prompt.isEmpty()) {
            prompt = getString(R.string.default_audio_prompt);
        }
        String sessionId = currentSessionId != null && !currentSessionId.isEmpty()
                ? currentSessionId
                : UUID.randomUUID().toString().substring(0, 12);
        uploadAudioFileInChunks(prompt, pendingAudioFile, sessionId);
    }

    private void cancelAudioRecording() {
        try {
            if (audioRecorder != null) {
                audioRecorder.stop();
            }
        } catch (RuntimeException ignored) {
        } finally {
            releaseAudioRecorder();
        }
        if (pendingAudioFile != null && pendingAudioFile.exists()) {
            pendingAudioFile.delete();
        }
        pendingAudioFile = null;
        binding.recordAudioButton.setText(R.string.record_audio);
    }

    private void releaseAudioRecorder() {
        audioRecordingActive = false;
        if (audioRecorder != null) {
            audioRecorder.reset();
            audioRecorder.release();
            audioRecorder = null;
        }
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
        liveTimedOutFrames = 0;
        liveCaptureIntervalMs = LIVE_CAPTURE_INTERVAL_BASE_MS;
        liveCaptureStartedAtMs = 0L;
        liveCaptureDeadlineAtMs = 0L;
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
            currentSessionId = UUID.randomUUID().toString().substring(0, 12);
            RequestBody sessionBody = RequestBody.create(currentSessionId, MediaType.parse("text/plain"));

            service.analyzeScene(imagePart, promptBody, sessionBody).enqueue(new Callback<AcceptedResponse>() {
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

    private void uploadAudioFile(String prompt, File audioFile, String sessionId) {
        RequestBody audioBody = RequestBody.create(audioFile, MediaType.parse("audio/m4a"));
        MultipartBody.Part audioPart = MultipartBody.Part.createFormData("audio", audioFile.getName(), audioBody);
        RequestBody promptBody = RequestBody.create(prompt, MediaType.parse("text/plain"));
        currentSessionId = sessionId;
        RequestBody sessionBody = RequestBody.create(sessionId, MediaType.parse("text/plain"));

        service.analyzeAudio(audioPart, promptBody, sessionBody).enqueue(new Callback<AcceptedResponse>() {
            @Override
            public void onResponse(@NonNull Call<AcceptedResponse> call, @NonNull Response<AcceptedResponse> response) {
                if (!response.isSuccessful() || response.body() == null) {
                    showError("Audio analyze failed: " + response.code());
                    cleanupPendingAudioFile();
                    return;
                }
                binding.recordAudioButton.setText(R.string.record_audio);
                binding.statusText.setText(R.string.status_audio_ready);
                binding.selectedFileLabel.setText(R.string.audio_recorded_label);
                cleanupPendingAudioFile();
                startEventStream(response.body().sessionId, response.body().runId);
            }

            @Override
            public void onFailure(@NonNull Call<AcceptedResponse> call, @NonNull Throwable t) {
                showError("Audio analyze failed: " + t.getMessage());
                cleanupPendingAudioFile();
            }
        });
    }

    private void uploadAudioFileInChunks(String prompt, File audioFile, String sessionId) {
        long totalBytes = audioFile.length();
        if (totalBytes <= 0) {
            binding.statusText.setText(R.string.status_audio_recording_failed);
            cleanupPendingAudioFile();
            return;
        }
        int totalChunks = (int) ((totalBytes + AUDIO_UPLOAD_CHUNK_BYTES - 1) / AUDIO_UPLOAD_CHUNK_BYTES);
        String uploadId = UUID.randomUUID().toString().substring(0, 12);
        uploadNextAudioChunk(prompt, audioFile, sessionId, uploadId, 0, totalChunks);
    }

    private void uploadNextAudioChunk(
            String prompt,
            File audioFile,
            String sessionId,
            String uploadId,
            int chunkIndex,
            int totalChunks
    ) {
        byte[] payload;
        try {
            payload = readAudioChunk(audioFile, chunkIndex);
        } catch (IOException exc) {
            showError("Audio chunk read failed: " + exc.getMessage());
            cleanupPendingAudioFile();
            return;
        }
        boolean finalChunk = chunkIndex == totalChunks - 1;
        MultipartBody.Part audioPart = buildAudioChunkPart(payload, chunkIndex, finalChunk);
        RequestBody promptBody = RequestBody.create(prompt, MediaType.parse("text/plain"));
        currentSessionId = sessionId;
        RequestBody sessionBody = RequestBody.create(sessionId, MediaType.parse("text/plain"));
        RequestBody uploadIdBody = RequestBody.create(uploadId, MediaType.parse("text/plain"));
        RequestBody chunkIndexBody = RequestBody.create(String.valueOf(chunkIndex), MediaType.parse("text/plain"));
        RequestBody finalChunkBody = RequestBody.create(String.valueOf(finalChunk), MediaType.parse("text/plain"));
        RequestBody audioExtBody = RequestBody.create(".m4a", MediaType.parse("text/plain"));

        binding.statusText.setText(getString(R.string.status_audio_chunk_uploading, chunkIndex + 1, totalChunks));
        service.uploadAudioChunk(
                audioPart,
                promptBody,
                sessionBody,
                uploadIdBody,
                chunkIndexBody,
                finalChunkBody,
                audioExtBody
        ).enqueue(new Callback<AudioChunkUploadResponse>() {
            @Override
            public void onResponse(@NonNull Call<AudioChunkUploadResponse> call, @NonNull Response<AudioChunkUploadResponse> response) {
                if (!response.isSuccessful() || response.body() == null) {
                    showError("Audio chunk upload failed: " + response.code());
                    cleanupPendingAudioFile();
                    return;
                }
                AudioChunkUploadResponse body = response.body();
                currentSessionId = body.sessionId;
                if (!body.finalized) {
                    uploadNextAudioChunk(prompt, audioFile, body.sessionId, uploadId, chunkIndex + 1, totalChunks);
                    return;
                }
                binding.recordAudioButton.setText(R.string.record_audio);
                binding.statusText.setText(getString(
                        R.string.status_audio_chunk_queued,
                        body.queuePosition != null ? body.queuePosition : 0
                ));
                binding.selectedFileLabel.setText(R.string.audio_recorded_label);
                cleanupPendingAudioFile();
                if (body.runId != null && !body.runId.isEmpty()) {
                    startEventStream(body.sessionId, body.runId);
                }
            }

            @Override
            public void onFailure(@NonNull Call<AudioChunkUploadResponse> call, @NonNull Throwable t) {
                showError("Audio chunk upload failed: " + t.getMessage());
                cleanupPendingAudioFile();
            }
        });
    }

    private byte[] readAudioChunk(File audioFile, int chunkIndex) throws IOException {
        long offset = (long) chunkIndex * AUDIO_UPLOAD_CHUNK_BYTES;
        long remaining = Math.max(0L, audioFile.length() - offset);
        int size = (int) Math.min(AUDIO_UPLOAD_CHUNK_BYTES, remaining);
        if (size <= 0) {
            throw new IOException("No audio bytes remain for chunk " + chunkIndex);
        }
        byte[] payload = new byte[size];
        try (RandomAccessFile raf = new RandomAccessFile(audioFile, "r")) {
            raf.seek(offset);
            raf.readFully(payload);
        }
        return payload;
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

    private void uploadImageBytes(String prompt, byte[] bytes, String fileName, String mimeType) {
        uploadImageBytes(
                prompt,
                bytes,
                fileName,
                mimeType,
                UUID.randomUUID().toString().substring(0, 12),
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

        service.analyzeScene(imagePart, promptBody, sessionBody).enqueue(new Callback<AcceptedResponse>() {
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
