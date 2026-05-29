package com.scenecopilot.app.network;

import com.scenecopilot.app.models.AcceptedResponse;
import com.scenecopilot.app.models.ActionCardExecuteRequest;
import com.scenecopilot.app.models.ActionCardExecuteResponse;
import com.scenecopilot.app.models.AudioChunkUploadResponse;
import com.scenecopilot.app.models.ChatRequest;
import com.scenecopilot.app.models.ClientIncidentRequest;
import com.scenecopilot.app.models.ClientIncidentResponse;
import com.scenecopilot.app.models.DocumentSearchResponse;
import com.scenecopilot.app.models.RunApprovalRequest;
import com.scenecopilot.app.models.RunApprovalResponse;
import com.scenecopilot.app.models.RunCancelResponse;
import com.scenecopilot.app.models.RunContinueResponse;
import com.scenecopilot.app.models.RunDetailResponse;
import com.scenecopilot.app.models.RunReplayResponse;
import com.scenecopilot.app.models.RunRetryResponse;

import okhttp3.MultipartBody;
import okhttp3.RequestBody;
import retrofit2.Call;
import retrofit2.http.Body;
import retrofit2.http.GET;
import retrofit2.http.Multipart;
import retrofit2.http.POST;
import retrofit2.http.Part;
import retrofit2.http.Path;
import retrofit2.http.Query;

public interface SceneCopilotService {
    @POST("api/chat")
    Call<AcceptedResponse> chat(@Body ChatRequest request);

    @Multipart
    @POST("api/scans/analyze")
    Call<AcceptedResponse> analyzeScene(
            @Part MultipartBody.Part image,
            @Part("prompt") RequestBody prompt,
            @Part("session_id") RequestBody sessionId,
            @Part("captured_at_ms") RequestBody capturedAtMs,
            @Part("capture_profile") RequestBody captureProfile
    );

    @Multipart
    @POST("api/audio/analyze")
    Call<AcceptedResponse> analyzeAudio(
            @Part MultipartBody.Part audio,
            @Part("prompt") RequestBody prompt,
            @Part("session_id") RequestBody sessionId,
            @Part("capture_profile") RequestBody captureProfile
    );

    @Multipart
    @POST("api/audio/chunk")
    Call<AudioChunkUploadResponse> uploadAudioChunk(
            @Part MultipartBody.Part audio,
            @Part("prompt") RequestBody prompt,
            @Part("session_id") RequestBody sessionId,
            @Part("upload_id") RequestBody uploadId,
            @Part("chunk_index") RequestBody chunkIndex,
            @Part("final_chunk") RequestBody finalChunk,
            @Part("audio_ext") RequestBody audioExt,
            @Part("audio_format") RequestBody audioFormat,
            @Part("window_started_at_ms") RequestBody windowStartedAtMs,
            @Part("window_ended_at_ms") RequestBody windowEndedAtMs,
            @Part("capture_profile") RequestBody captureProfile
    );

    @GET("api/documents/search")
    Call<DocumentSearchResponse> searchDocuments(
            @Query("q") String query,
            @Query("limit") int limit
    );

    @GET("api/runs/{runId}")
    Call<RunDetailResponse> getRun(@Path("runId") String runId);

    @GET("api/runs/{runId}/replay")
    Call<RunReplayResponse> replayRun(
            @Path("runId") String runId,
            @Query("limit") int limit
    );

    @POST("api/runs/{runId}/retry")
    Call<RunRetryResponse> retryRun(@Path("runId") String runId);

    @POST("api/runs/{runId}/cancel")
    Call<RunCancelResponse> cancelRun(@Path("runId") String runId);

    @Multipart
    @POST("api/runs/{runId}/continue")
    Call<RunContinueResponse> continueRun(
            @Path("runId") String runId,
            @Part MultipartBody.Part image,
            @Part MultipartBody.Part audio,
            @Part("visible_text") RequestBody visibleText
    );

    @POST("api/runs/{runId}/approve")
    Call<RunApprovalResponse> resolveApproval(
            @Path("runId") String runId,
            @Body RunApprovalRequest request
    );

    @POST("api/action-cards/{cardId}/execute")
    Call<ActionCardExecuteResponse> executeActionCard(
            @Path("cardId") int cardId,
            @Body ActionCardExecuteRequest request
    );

    @POST("api/client/incident")
    Call<ClientIncidentResponse> reportIncident(@Body ClientIncidentRequest request);
}
