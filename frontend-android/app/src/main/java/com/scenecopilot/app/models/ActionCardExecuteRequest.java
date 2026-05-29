package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class ActionCardExecuteRequest {
    @SerializedName("option_id")
    public final String optionId;

    @SerializedName("note")
    public final String note;

    public ActionCardExecuteRequest(String optionId, String note) {
        this.optionId = optionId;
        this.note = note;
    }
}
