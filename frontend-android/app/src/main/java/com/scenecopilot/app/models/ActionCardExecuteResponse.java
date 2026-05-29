package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

import java.util.Map;

public class ActionCardExecuteResponse {
    @SerializedName("card_id")
    public int cardId;

    @SerializedName("run_id")
    public String runId;

    @SerializedName("option_id")
    public String optionId;

    @SerializedName("status")
    public String status;

    @SerializedName("message")
    public String message;

    @SerializedName("continuation_run_id")
    public String continuationRunId;

    @SerializedName("continuation_queue_position")
    public Integer continuationQueuePosition;

    @SerializedName("continuation_state")
    public String continuationState;

    @SerializedName("evidence")
    public Map<String, Object> evidence;
}
