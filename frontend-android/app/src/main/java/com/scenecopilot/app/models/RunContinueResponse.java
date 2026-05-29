package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class RunContinueResponse {
    @SerializedName("session_id")
    public String sessionId;

    @SerializedName("run_id")
    public String runId;

    @SerializedName("state")
    public String state;

    @SerializedName("queue_position")
    public int queuePosition;
}
