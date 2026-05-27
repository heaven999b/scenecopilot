package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class AudioChunkUploadResponse {
    @SerializedName("accepted")
    public boolean accepted;

    @SerializedName("upload_id")
    public String uploadId;

    @SerializedName("session_id")
    public String sessionId;

    @SerializedName("received_chunk")
    public int receivedChunk;

    @SerializedName("finalized")
    public boolean finalized;

    @SerializedName("state")
    public String state;

    @SerializedName("run_id")
    public String runId;

    @SerializedName("queue_position")
    public Integer queuePosition;
}
