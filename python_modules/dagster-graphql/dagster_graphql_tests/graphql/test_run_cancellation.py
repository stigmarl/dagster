import os
import time

from dagster_graphql.test.utils import execute_dagster_graphql

from dagster import execute_pipeline
from dagster.utils import safe_tempfile_path

from .execution_queries import START_PIPELINE_EXECUTION_QUERY
from .graphql_context_test_suite import GraphQLContextVariant, make_graphql_context_test_suite

RUN_CANCELLATION_QUERY = '''
mutation($runId: String!) {
  terminatePipelineExecution(runId: $runId){
    __typename
    ... on TerminatePipelineExecutionSuccess{
      run {
        runId
      }
    }
    ... on TerminatePipelineExecutionFailure {
      run {
        runId
      }
      message
    }
    ... on PipelineRunNotFoundError {
      runId
    }
  }
}
'''


class TestRunVariantTermination(
    make_graphql_context_test_suite(
        context_variants=[GraphQLContextVariant.sqlite_subprocess_start()]
    )
):
    def test_basic_termination(self, graphql_context):
        with safe_tempfile_path() as path:
            result = execute_dagster_graphql(
                graphql_context,
                START_PIPELINE_EXECUTION_QUERY,
                variables={
                    'executionParams': {
                        'selector': {'name': 'infinite_loop_pipeline'},
                        'mode': 'default',
                        'environmentConfigData': {'solids': {'loop': {'config': {'file': path}}}},
                    }
                },
            )

            assert not result.errors
            assert result.data

            # just test existence
            assert result.data['startPipelineExecution']['__typename'] == 'StartPipelineRunSuccess'
            run_id = result.data['startPipelineExecution']['run']['runId']

            assert run_id

            # ensure the execution has happened
            while not os.path.exists(path):
                time.sleep(0.1)

            result = execute_dagster_graphql(
                graphql_context, RUN_CANCELLATION_QUERY, variables={'runId': run_id}
            )

            assert (
                result.data['terminatePipelineExecution']['__typename']
                == 'TerminatePipelineExecutionSuccess'
            )

    def test_run_not_found(self, graphql_context):
        result = execute_dagster_graphql(
            graphql_context, RUN_CANCELLATION_QUERY, variables={'runId': 'nope'}
        )
        assert result.data['terminatePipelineExecution']['__typename'] == 'PipelineRunNotFoundError'

    def test_terminate_failed(self, graphql_context):
        with safe_tempfile_path() as path:
            old_terminate = graphql_context.legacy_environment.execution_manager.terminate
            graphql_context.legacy_environment.execution_manager.terminate = lambda _run_id: False
            result = execute_dagster_graphql(
                graphql_context,
                START_PIPELINE_EXECUTION_QUERY,
                variables={
                    'executionParams': {
                        'selector': {'name': 'infinite_loop_pipeline'},
                        'mode': 'default',
                        'environmentConfigData': {'solids': {'loop': {'config': {'file': path}}}},
                    }
                },
            )

            assert not result.errors
            assert result.data

            # just test existence
            assert result.data['startPipelineExecution']['__typename'] == 'StartPipelineRunSuccess'
            run_id = result.data['startPipelineExecution']['run']['runId']
            # ensure the execution has happened
            while not os.path.exists(path):
                time.sleep(0.1)

            result = execute_dagster_graphql(
                graphql_context, RUN_CANCELLATION_QUERY, variables={'runId': run_id}
            )
            assert (
                result.data['terminatePipelineExecution']['__typename']
                == 'TerminatePipelineExecutionFailure'
            )
            assert result.data['terminatePipelineExecution']['message'].startswith(
                'Unable to terminate run'
            )

            graphql_context.legacy_environment.execution_manager.terminate = old_terminate

            result = execute_dagster_graphql(
                graphql_context, RUN_CANCELLATION_QUERY, variables={'runId': run_id}
            )

            assert (
                result.data['terminatePipelineExecution']['__typename']
                == 'TerminatePipelineExecutionSuccess'
            )

    def test_run_finished(self, graphql_context):
        instance = graphql_context.instance
        pipeline_result = execute_pipeline(
            graphql_context.legacy_environment.get_reconstructable_pipeline('noop_pipeline'),
            instance=instance,
        )
        assert pipeline_result.success
        assert pipeline_result.run_id

        time.sleep(0.05)  # guarantee execution finish

        result = execute_dagster_graphql(
            graphql_context, RUN_CANCELLATION_QUERY, variables={'runId': pipeline_result.run_id}
        )

        assert (
            result.data['terminatePipelineExecution']['__typename']
            == 'TerminatePipelineExecutionFailure'
        )
        assert (
            'is not in a started state. Current status is SUCCESS'
            in result.data['terminatePipelineExecution']['message']
        )
