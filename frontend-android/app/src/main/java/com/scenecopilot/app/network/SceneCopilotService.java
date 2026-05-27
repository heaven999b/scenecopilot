package com.scenecopilot.app.network;

import com.scenecopilot.app.models.AcceptedResponse;
import com.scenecopilot.app.models.ChatRequest;
import com.scenecopilot.app.models.DocumentSearchResponse;
import com.scenecopilot.app.models.RunApprovalRequest;
import com.scenecopilot.app.models.RunApprovalResponse;
import com.scenecopilot.app.models.RunDetailResponse;

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
            @Part("session_id") RequestBody sessionId
    );

    @GET("api/documents/search")
    Call<DocumentSearchResponse> searchDocuments(
            @Query("q") String query,
            @Query("limit") int limit
    );

    @GET("api/runs/{runId}")
    Call<RunDetailResponse> getRun(@Path("runId") String runId);

    @POST("api/runs/{runId}/approve")
    Call<RunApprovalResponse> resolveApproval(
            @Path("runId") String runId,
            @Body RunApprovalRequest request
    );
}
