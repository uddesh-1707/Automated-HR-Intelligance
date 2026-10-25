import { useState } from "react";

const appsScript = `const CONFIG = {
  endpoint: "https://YOUR-HRMS-DOMAIN/api/v1/recruitment/google-forms/applications",
  nameField: "Candidate name",
  emailField: "Email address",
  phoneField: "Phone number",
  jobField: "Job title",
  resumeField: "Resume"
};

function onFormSubmit(event) {
  const answers = event.namedValues;
  const fileAnswer = (answers[CONFIG.resumeField] || [])[0] || "";
  const fileId = fileAnswer.match(/[\\\\w-]{25,}/)?.[0];
  if (!fileId) throw new Error("The resume upload did not include a Google Drive file ID.");

  // DriveApp runs as the script owner and reads the private Form upload.
  const file = DriveApp.getFileById(fileId);
  const blob = file.getBlob();
  const payload = {
    name: (answers[CONFIG.nameField] || [""])[0].trim(),
    email: (answers[CONFIG.emailField] || [""])[0].trim(),
    phone: (answers[CONFIG.phoneField] || [""])[0].trim() || null,
    job_title: (answers[CONFIG.jobField] || [""])[0].trim(),
    resume_filename: file.getName(),
    resume_content_type: blob.getContentType(),
    resume_base64: Utilities.base64Encode(blob.getBytes())
  };
  const secret = PropertiesService.getScriptProperties().getProperty("HRMS_INTEGRATION_SECRET");
  if (!secret) throw new Error("Set HRMS_INTEGRATION_SECRET in Apps Script project properties.");
  const response = UrlFetchApp.fetch(CONFIG.endpoint, {
    method: "post",
    contentType: "application/json",
    headers: { "X-Integration-Key": secret },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  if (response.getResponseCode() < 200 || response.getResponseCode() >= 300) {
    throw new Error("HRMS rejected the application (" + response.getResponseCode() + "). Check the Apps Script execution log.");
  }
}`;

const fields = ["Candidate name (Short answer)", "Email address (Short answer, email validation)", "Phone number (Short answer, optional)", "Job title (Dropdown; exactly matches an active HRMS role)", "Resume (File upload; PDF, DOCX or TXT; maximum 5 MB)"];

export default function GoogleSetupPage() {
  const [copied, setCopied] = useState(false);
  async function copyScript() {
    try {
      await navigator.clipboard.writeText(appsScript);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div>
      <div className="page-heading"><div><div className="eyebrow">CANDIDATE INTAKE</div><h1>Google Forms setup<span className="heading-period">.</span></h1><p className="muted">Connect your form without making candidate applications public.</p></div><span className="integration-pill"><i /> Protected integration</span></div>
      <section className="guide-banner"><div className="guide-icon">⌁</div><div><b>Drive files are transferred securely.</b><p>The Apps Script runs as its owner, reads the uploaded file from Drive, then sends the file bytes to the HRMS endpoint over HTTPS. The form never calls a public upload URL.</p></div></section>
      <div className="guide-grid">
        <section className="panel guide-panel"><div className="step-heading"><span>01</span><div><h2>Prepare your form</h2><p>Use these question titles exactly, or update the matching values in the script.</p></div></div><ul className="field-list">{fields.map(field => <li key={field}><span>✓</span>{field}</li>)}</ul><div className="guide-tip"><b>Keep job choices current</b><p>Job title values must exactly match an active role created in Recruitment → Open a role. Disable closed roles in your form.</p></div></section>
        <section className="panel guide-panel"><div className="step-heading"><span>02</span><div><h2>Configure the backend</h2><p>Keep the integration key private and set it only in the backend environment.</p></div></div><div className="config-list"><div><span>Backend environment</span><code>GOOGLE_FORMS_INTEGRATION_SECRET</code></div><div><span>Maximum resume size</span><code>5 MB · PDF / DOCX / TXT</code></div><div><span>Required transport</span><code>HTTPS · POST · JSON</code></div></div><p className="warning-copy">Do not put the secret in the form, browser code, or a public repository. Add the same value to Apps Script Project Settings → Script Properties as <code>HRMS_INTEGRATION_SECRET</code>.</p></section>
      </div>
      <section className="panel script-panel"><div className="panel-heading"><div><div className="eyebrow">APPS SCRIPT</div><h2>Transfer the Drive upload</h2><p className="muted">Add a script at script.google.com, adjust the endpoint and question names, then add an installable form-submit trigger.</p></div><button className="button button-quiet" onClick={copyScript}>{copied ? "Copied" : "Copy script"}</button></div><pre><code>{appsScript}</code></pre></section>
      <section className="panel guide-panel deployment-panel"><div className="step-heading"><span>03</span><div><h2>Authorize & deploy</h2><p>Install the trigger on the form owner's account so it can read uploaded Drive files.</p></div></div><ol className="ordered-guide"><li>In Google Forms, open the linked Apps Script project and paste the script above.</li><li>Set the script properties <code>HRMS_INTEGRATION_SECRET</code> to exactly the backend's secret. In <code>CONFIG.endpoint</code>, use your deployed HTTPS API URL.</li><li>In Apps Script → Triggers, add <code>onFormSubmit</code> for the form's “On form submit” event. Accept the Drive and external-request permissions.</li><li>Submit one test response with a small supported resume. Check Executions in Apps Script and confirm the application appears in the HRMS “Needs review” stage.</li><li>If submissions fail, check the HTTP status in Apps Script executions and backend logs. Never log or share the integration secret or resume contents.</li></ol><div className="alert alert-info"><span>i</span>Email notifications and calendar invitations are not sent by this integration.</div></section>
    </div>
  );
}

