package com.scenecopilot.app.models;

import com.google.gson.Gson;
import com.google.gson.annotations.SerializedName;

import java.util.Map;

public class ReasoningEvent {
    @SerializedName("id")
    public Integer id;

    @SerializedName("session_id")
    public String sessionId;

    @SerializedName("run_id")
    public String runId;

    @SerializedName("event_type")
    public String eventType;

    @SerializedName("payload")
    public Map<String, Object> payload;

    public String getDisplayTitle() {
        if (eventType == null) {
            return "Event";
        }
        switch (eventType) {
            case "queued":
                return "Queued";
            case "run_started":
                return "Run started";
            case "user_message":
                return "User request";
            case "stage":
                return "Stage";
            case "policy":
                return "Policy";
            case "run_plan":
                return "Run plan";
            case "artifact":
                return "Artifact";
            case "approval":
                return "Approval needed";
            case "approval_resolved":
                return "Approval resolved";
            case "thinking":
                return "Thinking";
            case "tool_call":
                return "Tool call";
            case "tool_result":
                return "Tool result";
            case "final":
                return "Final answer";
            default:
                return eventType.replace("_", " ");
        }
    }

    public String getDisplayBody() {
        if (payload == null || payload.isEmpty()) {
            return "";
        }
        if (payload.containsKey("text")) {
            return String.valueOf(payload.get("text"));
        }
        if (payload.containsKey("text_delta")) {
            return String.valueOf(payload.get("text_delta"));
        }
        if (payload.containsKey("artifact_type")) {
            String artifactType = String.valueOf(payload.get("artifact_type"));
            if (payload.containsKey("summary")) {
                return artifactType + ": " + String.valueOf(payload.get("summary"));
            }
            if (payload.containsKey("preview")) {
                return artifactType + ": " + String.valueOf(payload.get("preview"));
            }
        }
        if (payload.containsKey("approval_status")) {
            return "status=" + payload.get("approval_status") + ", note=" + payload.get("reviewer_note");
        }
        if (payload.containsKey("ocr_reason") || payload.containsKey("retrieval_reason")) {
            return "OCR: " + payload.get("ocr_reason") + " | Retrieval: " + payload.get("retrieval_reason");
        }
        if (payload.containsKey("tool")) {
            Object result = payload.get("result");
            if (result != null) {
                return payload.get("tool") + ": " + new Gson().toJson(result);
            }
            Object input = payload.get("input");
            if (input != null) {
                return payload.get("tool") + ": " + new Gson().toJson(input);
            }
        }
        return new Gson().toJson(payload);
    }
}
