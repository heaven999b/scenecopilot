package com.scenecopilot.app.network;

import okhttp3.OkHttpClient;
import okhttp3.logging.HttpLoggingInterceptor;
import retrofit2.Retrofit;
import retrofit2.converter.gson.GsonConverterFactory;

public final class ApiClient {
    private static final String BASE_URL = "http://10.0.2.2:8002/";

    private static final HttpLoggingInterceptor LOGGING = new HttpLoggingInterceptor()
            .setLevel(HttpLoggingInterceptor.Level.BASIC);

    private static final OkHttpClient OK_HTTP_CLIENT = new OkHttpClient.Builder()
            .addInterceptor(LOGGING)
            .build();

    private static final Retrofit RETROFIT = new Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(OK_HTTP_CLIENT)
            .addConverterFactory(GsonConverterFactory.create())
            .build();

    private ApiClient() {
    }

    public static SceneCopilotService service() {
        return RETROFIT.create(SceneCopilotService.class);
    }

    public static OkHttpClient okHttpClient() {
        return OK_HTTP_CLIENT;
    }

    public static String baseUrl() {
        return BASE_URL;
    }
}
