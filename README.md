<div align="center">
  <h1>VertexaiForJanitors</h1>
  <p><b>Vertex AI Proxy for JanitorAI</b></p>
  <p>Run JanitorAI stably via Google Vertex AI to bypass 429 quota issues.</p>
</div>

---

## 📖 Introduction

This project is a fork/modification of [vu5eruz/GeminiForJanitors](https://github.com/vu5eruz/GeminiForJanitors).

While the original project targets Google AI Studio (Gemini API), this version is modified to support the enterprise-grade **Google Cloud Vertex AI**.

**Key Features:**
* ✅ **Stable Quotas**: Vertex AI quotas are generally higher than the free AI Studio tier, significantly reducing 429 errors. **ONlY ONE DRAWBACK IS NOT FREE, but you have $300 for free in 3 months if you have a card**
* ✅ **Uncensored Support**: Vertex AI safety filters are easier to configure via code, allowing for better handling of NSFW content without immediate blocking.

---

## 🛠️ Prerequisites

Before deploying, you need to obtain two key pieces of information from Google Cloud.

### 1. Create a Google Cloud Project
If you don't have one, go to the [Google Cloud Console](https://console.cloud.google.com/) and create a **New Project**.


### 2. Enable Vertex AI API
Select your project and click the link below to enable the API:
👉 [Enable Vertex AI API](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com)

### 3. Get Service Account Key (JSON)
This is the most critical step. Please follow these instructions carefully:

1.  Go to [IAM & Admin -> Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).
2.  Click **+ CREATE SERVICE ACCOUNT** at the top.
3.  **Step 1**: Enter any name (e.g., `janitor-bot`) and click "Create and Continue".
4.  **Step 2 (Crucial)**: In the "Select a role" dropdown, search for and select **`Vertex AI User`**.
    * *If you skip this, your key will not work!*
5.  Click "Done".
6.  Click on the newly created service account (the email link) in the list.
7.  Go to the **KEYS** tab -> **Add Key** -> **Create new key** -> Select **JSON**.
8.  A `.json` file will automatically download. **Keep this file safe and copy its entire content.**

---

## 🚀 Deployment Guide

Recommended: Deploy for free using **Render**.

### 1. Import Code
Fork this repository to your GitHub, then create a new **Web Service** on Render and connect your repository.

### 2. Configure Start Commands
* **Build Command:**
    ```bash
    pip install -r requirements.txt && uv sync --locked --all-extras --dev
    ```
* **Start Command:**
    ```bash
    uv run gunicorn -b 0.0.0.0:$PORT -k gevent -w 1 -t 90 gfjproxy.app:app
    ```

### 3. Set Environment Variables
Go to the **Environment** tab in Render and add the following two variables:

| Key | Value | Description |
| :--- | :--- | :--- |
| **`PROJECT_ID`** | `your-project-id` | Your Google Cloud Project ID (e.g., `name-12345`). |
| **`GOOGLE_CREDENTIALS`** | `{ "type": ... }` | **Paste the entire content** of the JSON file you downloaded earlier. |
---
