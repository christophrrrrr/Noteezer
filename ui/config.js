// Where the two services are. This copy is for running locally: docker
// compose and the Azure deploy write their own config.js over it (ADR-8).
window.NOTEEZER = {
  inboxUrl: "http://localhost:8000",
  remindersUrl: "http://localhost:8001",
};
