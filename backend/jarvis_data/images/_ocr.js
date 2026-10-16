
ObjC.import('Vision'); ObjC.import('Foundation');
function run(argv) {
  const url = $.NSURL.fileURLWithPath(argv[0]);
  const handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $({}));
  const req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0;
  req.usesLanguageCorrection = true;
  handler.performRequestsError($([req]), null);
  const res = req.results; const out = [];
  for (let i = 0; i < res.count; i++) {
    const c = res.objectAtIndex(i).topCandidates(1);
    if (c.count) out.push(c.objectAtIndex(0).string.js);
  }
  return out.join("\n");
}
