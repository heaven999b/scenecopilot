package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class RunApprovalRequest {
    @SerializedName("decision")
    public final String decision;

    @SerializedName("reviewer_note")
    public final String reviewerNote;

    public RunApprovalRequest(String decision, String reviewerNote) {
        this.decision = decision;
        this.reviewerNote = reviewerNote;
    }
}
