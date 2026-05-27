package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class RunDetailResponse {
    @SerializedName("id")
    public String id;

    @SerializedName("session_id")
    public String sessionId;

    @SerializedName("status")
    public String status;

    @SerializedName("route_name")
    public String routeName;

    @SerializedName("current_stage")
    public String currentStage;

    @SerializedName("output_text")
    public String outputText;

    @SerializedName("latency_ms")
    public Double latencyMs;

    @SerializedName("artifacts")
    public List<Map<String, Object>> artifacts = new ArrayList<>();

    @SerializedName("approvals")
    public List<Map<String, Object>> approvals = new ArrayList<>();

    @SerializedName("audit_log")
    public List<Map<String, Object>> auditLog = new ArrayList<>();

    @SerializedName("scene_captures")
    public List<Map<String, Object>> sceneCaptures = new ArrayList<>();

    @SerializedName("action_cards")
    public List<Map<String, Object>> actionCards = new ArrayList<>();

    public boolean isAwaitingApproval() {
        return "waiting_for_approval".equals(status);
    }

    public boolean isTerminal() {
        return "completed".equals(status)
                || "failed".equals(status)
                || "cancelled".equals(status);
    }
}
