package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class RunReplayResponse {
    @SerializedName("run_id")
    public String runId;

    @SerializedName("session_id")
    public String sessionId;

    @SerializedName("status")
    public String status;

    @SerializedName("current_stage")
    public String currentStage;

    @SerializedName("event_count")
    public int eventCount;

    @SerializedName("latest_event_id")
    public Integer latestEventId;

    @SerializedName("events")
    public List<ReasoningEvent> events = new ArrayList<>();

    @SerializedName("timings_json")
    public Map<String, Object> timingsJson;
}
