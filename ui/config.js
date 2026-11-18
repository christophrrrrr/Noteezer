// Where the two services are. This copy is for running locally: docker
// compose and the Azure deploy (scripts/build_ui.py) write their own config.js over it (ADR-6).
window.NOTEEZER = {
  inboxUrl: "http://localhost:8000",
  remindersUrl: "http://localhost:8001",
};
