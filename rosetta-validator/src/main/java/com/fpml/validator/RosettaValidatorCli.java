package com.fpml.validator;

import cdm.event.common.Trade;
import cdm.event.common.TradeState;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.google.inject.Guice;
import com.google.inject.Injector;
import com.regnosys.rosetta.common.hashing.ReferenceResolverProcessStep;
import com.regnosys.rosetta.common.serialisation.RosettaObjectMapper;
import com.regnosys.rosetta.common.validation.RosettaTypeValidator;
import com.regnosys.rosetta.common.validation.ValidationReport;
import com.rosetta.model.lib.validation.ValidationResult;
import org.finos.cdm.CdmRuntimeModule;
import org.isda.cdm.processor.CdmReferenceConfig;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * CLI tool: validates CDM JSON against the Rosetta type system.
 *
 * Usage: java -jar rosetta-validator.jar <cdm-json-file> [--type trade|tradeState]
 *
 * Reads CDM JSON, deserializes into CDM Java objects, runs RosettaTypeValidator,
 * and prints validation results as JSON to stdout.
 *
 * Exit codes: 0 = valid, 1 = validation failures, 2 = runtime error
 */
public class RosettaValidatorCli {

    public static void main(String[] args) {
        if (args.length < 1) {
            System.err.println("Usage: java -jar rosetta-validator.jar <cdm-json-file> [--type trade|tradeState]");
            System.exit(2);
        }

        String filePath = args[0];
        String targetType = "trade";
        for (int i = 1; i < args.length; i++) {
            if ("--type".equals(args[i]) && i + 1 < args.length) {
                targetType = args[++i];
            }
        }

        try {
            String json = Files.readString(Path.of(filePath), StandardCharsets.UTF_8);

            ObjectMapper rosettaMapper = RosettaObjectMapper.getNewRosettaObjectMapper();
            Injector injector = Guice.createInjector(new CdmRuntimeModule());
            RosettaTypeValidator validator = injector.getInstance(RosettaTypeValidator.class);

            ValidationReport report;

            if ("tradeState".equalsIgnoreCase(targetType)) {
                TradeState tradeState = rosettaMapper.readValue(json, TradeState.class);
                TradeState.TradeStateBuilder builder = tradeState.toBuilder();
                resolveReferences(builder);
                report = validator.runProcessStep(TradeState.class, builder);
            } else {
                JsonNode root = rosettaMapper.readTree(json);
                JsonNode tradeNode = root.has("trade") ? root.get("trade") : root;
                Trade trade = rosettaMapper.treeToValue(tradeNode, Trade.class);
                Trade.TradeBuilder builder = trade.toBuilder();
                resolveReferences(builder);
                report = validator.runProcessStep(Trade.class, builder);
            }

            ObjectMapper outputMapper = new ObjectMapper();
            ObjectNode result = outputMapper.createObjectNode();
            result.put("valid", report.success());

            ArrayNode failures = outputMapper.createArrayNode();
            List<? extends ValidationResult<?>> validationFailures = report.validationFailures();
            if (validationFailures != null) {
                for (ValidationResult<?> vr : validationFailures) {
                    ObjectNode entry = outputMapper.createObjectNode();
                    entry.put("name", vr.getName() != null ? vr.getName() : "");
                    entry.put("type", vr.getValidationType() != null ? vr.getValidationType().name() : "UNKNOWN");
                    entry.put("path", vr.getPath() != null ? vr.getPath().toString() : "");
                    entry.put("definition", vr.getDefinition() != null ? vr.getDefinition() : "");
                    entry.put("failureMessage", vr.getFailureReason() != null
                            ? vr.getFailureReason().orElse("") : "");
                    failures.add(entry);
                }
            }
            result.set("failures", failures);
            result.put("failureCount", failures.size());

            System.out.println(outputMapper.writerWithDefaultPrettyPrinter().writeValueAsString(result));
            System.exit(report.success() ? 0 : 1);

        } catch (Exception e) {
            ObjectMapper errMapper = new ObjectMapper();
            try {
                ObjectNode errResult = errMapper.createObjectNode();
                errResult.put("valid", false);
                errResult.put("error", e.getClass().getSimpleName() + ": " + e.getMessage());
                errResult.set("failures", errMapper.createArrayNode());
                errResult.put("failureCount", 0);
                System.out.println(errMapper.writerWithDefaultPrettyPrinter().writeValueAsString(errResult));
            } catch (Exception ignored) {
                System.err.println("Fatal: " + e.getMessage());
            }
            System.exit(2);
        }
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private static void resolveReferences(com.rosetta.model.lib.RosettaModelObject builder) {
        new ReferenceResolverProcessStep(CdmReferenceConfig.get())
                .runProcessStep(builder.getType(), builder);
    }
}
